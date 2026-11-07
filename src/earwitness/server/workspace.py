"""Server-side workspace: the opened recordings, their decoded samples, and display-size views.

The UI never touches audio itself. It opens a local file by path, the server decodes it once at
the working rate (4000 Hz, enough for the 20 to 600 Hz band both methods use) and keeps the
mono samples, and every view the UI asks for (spectrogram, envelope, ridge, fit) is computed
from that copy and cut down to display size before it leaves the process. Analysis runs land in
the runs folder as the same project folders the CLI writes, so the UI can reopen them later.
"""

from __future__ import annotations

import json
import math
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from earwitness.audio.decode import read_audio
from earwitness.audio.envelope import Envelope, EnvelopeConfig, observed_envelope, wind_mask
from earwitness.audio.spectrogram import Spectrogram, spectrogram
from earwitness.data.cache import cache_root
from earwitness.project import sha256_file, utc_now
from earwitness.server.jobs import JobRunner

WORKING_RATE_HZ = 4000
"""Decode rate for the views; both methods live below 600 Hz."""
FloatArray = NDArray[np.float64]


@dataclass
class Recording:
    """One opened recording with its decoded samples and cached views."""

    id: str
    path: Path
    sha256: str
    rate: int
    samples: FloatArray
    _spectrograms: dict[tuple[int, int, float], Spectrogram] = field(
        default_factory=dict, repr=False
    )
    _envelope: Envelope | None = field(default=None, repr=False)
    _wind: NDArray[np.bool_] | None = field(default=None, repr=False)
    _detection: object | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def duration_s(self) -> float:
        """Clip length, s."""
        return self.samples.size / self.rate

    def info(self) -> dict[str, object]:
        """What the UI shows about an opened file."""
        return {
            "id": self.id,
            "path": str(self.path),
            "name": self.path.name,
            "sha256": self.sha256,
            "duration_s": self.duration_s,
            "rate_hz": self.rate,
        }

    def spectrogram(self, nfft: int = 2048, hop: int = 512, fmax: float = 600.0) -> Spectrogram:
        """Whitened spectrogram at these settings, computed once and cached."""
        key = (int(nfft), int(hop), float(fmax))
        with self._lock:
            spec = self._spectrograms.get(key)
            if spec is None:
                spec = spectrogram(self.samples, self.rate, nfft=key[0], hop=key[1], fmax=key[2])
                self._spectrograms[key] = spec
            return spec

    def envelope(self) -> Envelope:
        """The thunder method's observed envelope (20 to 250 Hz loudness), cached."""
        with self._lock:
            if self._envelope is None:
                self._envelope = observed_envelope(self.samples, self.rate, EnvelopeConfig())
            return self._envelope

    def wind(self) -> NDArray[np.bool_]:
        """Per-envelope-frame wind flags, cached."""
        with self._lock:
            if self._wind is None:
                self._wind = wind_mask(self.samples, self.rate, EnvelopeConfig())
            return self._wind

    def detection(self) -> object:
        """The automatically measured flyovers (`aircraft.detect.Detection`), cached."""
        from earwitness.aircraft.detect import (
            RATE_HZ,
            DetectConfig,
            detect_flyovers,
            detection_spectrogram,
        )

        with self._lock:
            if self._detection is None:
                if self.rate != RATE_HZ:
                    raise ValueError(f"recordings are decoded at {self.rate} Hz, not {RATE_HZ}")
                cfg = DetectConfig()
                spec = detection_spectrogram(self.samples, self.rate, cfg)
                self._detection = detect_flyovers(spec, cfg)
            return self._detection


def block_reduce(values: FloatArray, factor: int, axis: int, how: str = "max") -> FloatArray:
    """Max or mean over consecutive blocks of `factor` along `axis`; a short last block counts."""
    if factor <= 1:
        return np.asarray(values, dtype=float)
    v = np.moveaxis(np.asarray(values, dtype=float), axis, 0)
    pad = (-v.shape[0]) % factor
    if pad:
        v = np.concatenate([v, np.full((pad, *v.shape[1:]), np.nan)], axis=0)
    v = v.reshape(v.shape[0] // factor, factor, *v.shape[1:])
    out = np.nanmax(v, axis=1) if how == "max" else np.nanmean(v, axis=1)
    return np.moveaxis(out, 0, axis)


def spectrogram_view(
    spec: Spectrogram,
    t_lo: float | None = None,
    t_hi: float | None = None,
    f_lo: float | None = None,
    f_hi: float | None = None,
    max_cols: int = 1200,
    max_rows: int = 300,
) -> dict[str, object]:
    """Crop the whitened spectrogram to a time and frequency window and reduce it to display size.

    Blocks are reduced by their maximum so a thin Doppler line survives the reduction; times and
    frequencies become block centers. Values are rounded to 0.1 dB to keep the JSON small.
    """
    t_mask = np.ones(spec.t.size, dtype=bool)
    if t_lo is not None:
        t_mask &= spec.t >= t_lo
    if t_hi is not None:
        t_mask &= spec.t <= t_hi
    f_mask = np.ones(spec.f.size, dtype=bool)
    if f_lo is not None:
        f_mask &= spec.f >= f_lo
    if f_hi is not None:
        f_mask &= spec.f <= f_hi
    t_idx = np.flatnonzero(t_mask)
    f_idx = np.flatnonzero(f_mask)
    if t_idx.size == 0 or f_idx.size == 0:
        return {"t": [], "f": [], "db": [], "t_step_s": None, "f_step_hz": None}
    db = spec.whitened_db[np.ix_(f_idx, t_idx)]
    t = spec.t[t_idx]
    f = spec.f[f_idx]
    ct = math.ceil(t.size / max_cols)
    cf = math.ceil(f.size / max_rows)
    db = block_reduce(block_reduce(db, ct, 1), cf, 0)
    t = block_reduce(t, ct, 0, "mean")
    f = block_reduce(f, cf, 0, "mean")
    return {
        "t": np.round(t, 3).tolist(),
        "f": np.round(f, 3).tolist(),
        "db": np.round(db, 1).tolist(),
        "t_step_s": float(spec.t[1] - spec.t[0]) * ct if spec.t.size > 1 else None,
        "f_step_hz": float(spec.f[1] - spec.f[0]) * cf if spec.f.size > 1 else None,
    }


def envelope_view(
    env: Envelope, wind: NDArray[np.bool_] | None = None, max_points: int = 3000
) -> dict[str, object]:
    """Reduce an envelope (and its wind flags) to display size; a block is windy if any frame is."""
    factor = math.ceil(env.t.size / max_points) if env.t.size else 1
    values = block_reduce(env.values, factor, 0)
    t = block_reduce(env.t, factor, 0, "mean")
    flags: list[bool] = []
    if wind is not None and wind.size:
        w = np.zeros(env.t.size, dtype=float)
        n = min(w.size, wind.size)
        w[:n] = wind[:n]
        flags = (block_reduce(w, factor, 0) > 0.0).tolist()
    return {
        "t": np.round(t, 3).tolist(),
        "values": np.round(values, 6).tolist(),
        "wind": flags,
        "frame_s": env.frame_s * factor,
        "wind_fraction": float(np.mean(wind)) if wind is not None and wind.size else 0.0,
    }


def run_card(run_id: str, summary: dict[str, object]) -> dict[str, object]:
    """The one-line description of a run folder the UI lists."""
    schema = str(summary.get("schema", ""))
    method = "thunder" if "thunder" in schema else "aircraft" if "aircraft" in schema else "?"
    top = summary.get("top") or []
    best = top[0] if isinstance(top, list) and top else None
    recording = summary.get("recording")
    return {
        "id": run_id,
        "method": method,
        "mode": summary.get("mode"),
        "recording": recording.get("file") if isinstance(recording, dict) else None,
        "runtime_s": summary.get("runtime_s"),
        "n_top": len(top) if isinstance(top, list) else 0,
        "best": None
        if not isinstance(best, dict)
        else {"lat": best.get("lat"), "lon": best.get("lon"), "score": best.get("score")},
    }


class Workspace:
    """Everything the server holds between requests."""

    def __init__(self, cache: str | Path | None = None, runs_dir: str | Path | None = None) -> None:
        self.cache_override = None if cache is None else Path(cache)
        self.cache_root = cache_root(cache)
        self.runs_dir = Path(runs_dir) if runs_dir is not None else Path("runs")
        self.recordings: dict[str, Recording] = {}
        self.jobs = JobRunner()
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()

    def open_recording(self, path: str | Path) -> Recording:
        """Decode a local file once (by content hash) and keep it; reopening is free."""
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        digest = sha256_file(p)
        rid = digest[:12]
        with self._lock:
            existing = self.recordings.get(rid)
        if existing is not None:
            return existing
        samples, rate = read_audio(p, target_rate=WORKING_RATE_HZ)
        rec = Recording(id=rid, path=p.resolve(), sha256=digest, rate=rate, samples=samples)
        with self._lock:
            self.recordings[rid] = rec
        return rec

    def recording(self, rid: str) -> Recording:
        """An opened recording by id; KeyError when it is not open."""
        with self._lock:
            return self.recordings[rid]

    def new_run_dir(self, stem: str, method: str) -> Path:
        """A fresh run folder under the runs folder, named like the CLI names its runs."""
        base = f"{utc_now():%Y%m%dT%H%M%SZ}-{stem}-{method}"
        out = self.runs_dir / base
        n = 1
        while out.exists():
            n += 1
            out = self.runs_dir / f"{base}-{n}"
        return out

    def run_dir(self, run_id: str) -> Path:
        """The folder of a finished run under the runs folder; KeyError for anything else."""
        if not run_id or run_id in (".", "..") or Path(run_id).name != run_id:
            raise KeyError(run_id)
        out = self.runs_dir / run_id
        if not (out / "summary.json").is_file():
            raise KeyError(run_id)
        return out

    def list_runs(self) -> list[dict[str, object]]:
        """Runs in the runs folder, newest first."""
        cards: list[dict[str, object]] = []
        if not self.runs_dir.is_dir():
            return cards
        folders = [d for d in self.runs_dir.iterdir() if (d / "summary.json").is_file()]
        for d in sorted(folders, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(summary, dict):
                cards.append(run_card(d.name, summary))
        return cards
