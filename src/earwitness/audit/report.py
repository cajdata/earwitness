"""Audit mode: which location clues does a recording contain?

The report is the first thing the UI and the CLI offer ("does my recording leak where it was
made?"), so it has to be honest about what it can and cannot detect. Four clue detectors cover
the two methods this project implements, mains hum (the cheapest clue of all), and the file's
own metadata. Each verdict comes with its evidence numbers, what the clue reveals, and what has
been shown to help against it. The detectors are heuristics tuned on synthetic cases, not
validated classifiers; see docs/VALIDATION.md for what has actually been tested.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.signal import welch

from earwitness.audio.decode import ffmpeg_path, read_audio
from earwitness.audio.envelope import EnvelopeConfig, observed_envelope, wind_mask
from earwitness.audio.spectrogram import ridge, spectrogram
from earwitness.project import sha256_file

SCHEMA = "earwitness.audit/1"
AUDIT_RATE_HZ = 4000
FloatArray = NDArray[np.float64]

METADATA_KEYWORDS = (
    "creation",
    "date",
    "time",
    "location",
    "gps",
    "latitude",
    "longitude",
    "encoder",
    "make",
    "model",
    "software",
    "comment",
    "artist",
    "title",
    "copyright",
    "device",
)
LOCATION_KEYWORDS = ("location", "gps", "latitude", "longitude")
TIME_KEYWORDS = ("creation", "date", "time")


@dataclass
class Clue:
    """One kind of location clue, with its verdict and evidence."""

    kind: str
    label: str
    present: bool
    confidence: str
    """none, weak or likely."""
    summary: str
    evidence: dict[str, object]
    leaks: str
    countermeasure: str


@dataclass
class AuditReport:
    """The audit of one file."""

    schema: str
    file: dict[str, object]
    metadata: dict[str, object]
    clues: list[Clue]

    @property
    def any_present(self) -> bool:
        """True when at least one clue was found."""
        return any(c.present for c in self.clues)

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form."""
        return {
            "schema": self.schema,
            "file": self.file,
            "metadata": self.metadata,
            "clues": [asdict(c) for c in self.clues],
            "any_present": self.any_present,
        }

    def text(self) -> str:
        """The report as the CLI prints it."""
        lines = [f"Earwitness audit: {self.file.get('name')}"]
        lines.append(
            f"  {self.file.get('duration_s', 0.0):.1f} s, analysis rate"
            f" {self.file.get('rate_hz')} Hz,"
            f" sha256 {str(self.file.get('sha256', ''))[:16]}..."
        )
        if self.metadata.get("available"):
            parts = [
                f"{k}: {self.metadata[k]}"
                for k in ("container", "codec", "sample_rate_hz", "channels")
                if self.metadata.get(k) is not None
            ]
            if parts:
                lines.append("  source: " + ", ".join(parts))
        else:
            lines.append("  source metadata: ffprobe not available, container tags not read")
        lines.append("")
        lines.append(f"  {'clue':<12} {'verdict':<8} evidence")
        for c in self.clues:
            lines.append(f"  {c.label:<12} {c.confidence:<8} {c.summary}")
        present = [c for c in self.clues if c.present]
        if present:
            lines.append("")
            lines.append("What the clues found could reveal, and what has been shown to help:")
            for c in present:
                lines.append(f"  {c.label}: {c.leaks}")
                lines.append(f"    Countermeasure: {c.countermeasure}")
        else:
            lines.append("")
            lines.append(
                "No clue of the kinds this tool checks for. That is not proof the recording holds"
                " none: other clues exist (docs/FUTURE_SOURCES.md), and faint signals can escape"
                " these heuristics."
            )
        return "\n".join(lines)


def _runs(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """Start and end (exclusive) index of each run of True."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, flag in enumerate(np.append(mask, False)):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i))
            start = None
    return out


def _absent(kind: str, label: str, summary: str, leaks: str, counter: str) -> Clue:
    return Clue(kind, label, False, "none", summary, {}, leaks, counter)


THUNDER_LEAKS = (
    "Loudness bursts in the 20 to 250 Hz band spaced like thunder. Matched against lightning"
    " records for the day, the pattern of arrival delays can place a recording to a few"
    " kilometres, and a long clip with many claps does better than a short one."
)
THUNDER_COUNTER = (
    "Cuts in the footage break the timing between claps, which is what the matching relies on."
    " Heavy noise reduction removes the quieter claps. Keep in mind that traffic, doors and wind"
    " also make low bursts, so this detector flags more than thunder."
)
AIRCRAFT_LEAKS = (
    "Tonal lines that slide downward the way a propeller aircraft sounds as it passes. Matched"
    " against ADS-B tracks for the day, two flyovers, or one flyover with a known start time,"
    " can place a recording to about 100 m."
)
AIRCRAFT_COUNTER = (
    "Cuts between flyovers break the timing the two-event search needs. In the published tests"
    " a high-pass filter did not remove the curve once the gain was restored, and a one minute"
    " clip with a single flyover was still solvable when its start time was known."
)
HUM_LEAKS = (
    "Mains hum at 60 Hz points to the Americas, Taiwan, South Korea, parts of Japan and Saudi"
    " Arabia; 50 Hz to nearly everywhere else. Grid frequency also wanders over the day, so with"
    " a reference log the hum can date a recording (ENF analysis, not implemented here)."
)
HUM_COUNTER = "A notch filter at the mains frequency and its harmonics removes the lines."
METADATA_LEAKS = (
    "The file itself says when or where it was made. Phone videos often embed GPS coordinates"
    " and a creation time; either one skips the acoustic search entirely or narrows it to a day."
)
METADATA_COUNTER = (
    "Strip metadata before sharing, for example `ffmpeg -i in.mp4 -map_metadata -1 -c copy"
    " out.mp4`, and check the result with this audit."
)


def thunder_clue(samples: FloatArray, rate: int) -> Clue:
    """Loudness bursts in the thunder band: count, spacing and height over the median."""
    cfg = EnvelopeConfig(smoothing_sigma_s=1.0)
    env = observed_envelope(samples, rate, cfg)
    v = env.values
    label = "thunder"
    if v.size < 16:
        return _absent("thunder", label, "clip too short", THUNDER_LEAKS, THUNDER_COUNTER)
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826
    threshold = med + 4.0 * max(mad, 1e-3 * med, 1e-12)
    bursts = [(a, b) for a, b in _runs(v > threshold) if 0.75 <= (b - a) * env.frame_s <= 90.0]
    n = len(bursts)
    centers = np.array([0.5 * (a + b) * env.frame_s for a, b in bursts])
    spacing = float(np.median(np.diff(centers))) if n >= 2 else None
    peak_db = 20.0 * math.log10(float(np.max(v)) / med) if med > 0 else 0.0
    wind_fraction = float(np.mean(wind_mask(samples, rate, cfg)))
    confidence = "likely" if n >= 5 and peak_db >= 8.0 else "weak" if n >= 2 else "none"
    summary = f"{n} loudness bursts in 20 to 250 Hz"
    if spacing is not None:
        summary += f", median spacing {spacing:.0f} s"
    summary += f", loudest {peak_db:.0f} dB over the median"
    if wind_fraction > 0.1:
        summary += f", wind flagged on {100 * wind_fraction:.0f}% of frames"
    evidence = {
        "bursts": n,
        "median_spacing_s": spacing,
        "peak_over_median_db": round(peak_db, 1),
        "wind_fraction": round(wind_fraction, 3),
        "burst_times_s": [round(float(c), 1) for c in centers[:50]],
    }
    return Clue(
        "thunder", label, confidence != "none", confidence, summary, evidence, THUNDER_LEAKS,
        THUNDER_COUNTER,
    )  # fmt: skip


def aircraft_clue(samples: FloatArray, rate: int) -> Clue:
    """Sweeping tonal ridges in the propeller band: bright for 10 s or more and falling."""
    label = "aircraft"
    band = (55.0, 190.0)
    if samples.size < rate * 20:
        return _absent("aircraft", label, "clip too short", AIRCRAFT_LEAKS, AIRCRAFT_COUNTER)
    spec = spectrogram(samples, rate, nfft=2048, hop=512, fmax=400.0)
    t, f = ridge(spec, *band)
    rows = (spec.f >= band[0]) & (spec.f <= band[1])
    bright = spec.whitened_db[rows].max(axis=0)
    dt = float(t[1] - t[0]) if t.size > 1 else 1.0
    k = max(1, round(2.0 / dt))
    bright_s = np.convolve(bright, np.ones(k) / k, mode="same")
    strong = bright_s > 6.0
    jumps = np.abs(np.diff(f)) > 0.12 * f[:-1]
    segments: list[dict[str, float]] = []
    for a, b in _runs(strong):
        pieces: list[tuple[int, int]] = []
        start = a
        for i in range(a, b - 1):
            if jumps[i]:
                pieces.append((start, i + 1))
                start = i + 1
        pieces.append((start, b))
        for p0, p1 in pieces:
            if (p1 - p0) * dt < 10.0 or p1 - p0 < 8:
                continue
            tt, ff = t[p0:p1], f[p0:p1]
            q = max(1, (p1 - p0) // 4)
            f_start = float(np.median(ff[:q]))
            f_end = float(np.median(ff[-q:]))
            mid = 0.5 * (f_start + f_end)
            drop = (f_start - f_end) / mid if mid > 0 else 0.0
            corr = float(np.corrcoef(tt, ff)[0, 1]) if np.std(ff) > 0 else 0.0
            if 0.02 <= drop <= 0.6 and corr < -0.6:
                segments.append(
                    {
                        "start_s": round(float(tt[0]), 1),
                        "end_s": round(float(tt[-1]), 1),
                        "f_start_hz": round(f_start, 1),
                        "f_end_hz": round(f_end, 1),
                        "drop_pct": round(100.0 * drop, 1),
                        "brightness_db": round(float(bright_s[p0:p1].max()), 1),
                    }
                )
    n = len(segments)
    long_sweep = any(s["drop_pct"] >= 8.0 and s["end_s"] - s["start_s"] >= 30.0 for s in segments)
    confidence = "likely" if n >= 2 or long_sweep else "weak" if n == 1 else "none"
    if n:
        best = max(segments, key=lambda s: s["brightness_db"])
        summary = (
            f"{n} falling tonal sweep{'s' if n != 1 else ''} in {band[0]:.0f} to {band[1]:.0f} Hz;"
            f" brightest {best['f_start_hz']:.0f} to {best['f_end_hz']:.0f} Hz over"
            f" {best['end_s'] - best['start_s']:.0f} s at {best['start_s']:.0f} s"
        )
    else:
        summary = f"no falling tonal sweep in {band[0]:.0f} to {band[1]:.0f} Hz"
    evidence = {"sweeps": n, "band_hz": list(band), "segments": segments[:10]}
    return Clue(
        "aircraft", label, confidence != "none", confidence, summary, evidence, AIRCRAFT_LEAKS,
        AIRCRAFT_COUNTER,
    )  # fmt: skip


def mains_hum_clue(samples: FloatArray, rate: int) -> Clue:
    """Spectral lines at 50 or 60 Hz and their harmonics, in dB over their surroundings."""
    label = "mains hum"
    if samples.size < rate * 4:
        return _absent("mains_hum", label, "clip too short", HUM_LEAKS, HUM_COUNTER)
    nper = int(min(16384, 2 ** math.floor(math.log2(samples.size))))
    freqs, power = welch(samples, fs=rate, nperseg=nper)

    def line_db(f0: float) -> float:
        near = (freqs >= f0 - 0.6) & (freqs <= f0 + 0.6)
        around = (
            (freqs >= f0 - 6.0) & (freqs <= f0 + 6.0) & ~((freqs >= f0 - 1.2) & (freqs <= f0 + 1.2))
        )
        if not near.any() or not around.any():
            return 0.0
        floor = max(float(np.median(power[around])), float(np.finfo(float).tiny))
        return float(10.0 * np.log10(float(power[near].max()) / floor))

    table = {
        50: [round(line_db(50.0 * n), 1) for n in (1, 2, 3)],
        60: [round(line_db(60.0 * n), 1) for n in (1, 2, 3)],
    }
    strong = {k: sum(x > 8.0 for x in v) for k, v in table.items()}
    nominal = max((50, 60), key=lambda k: (strong[k], table[k][0]))
    n_lines = strong[nominal]
    confidence = "likely" if n_lines >= 2 else "weak" if n_lines == 1 else "none"
    if n_lines:
        summary = (
            f"{nominal} Hz line {table[nominal][0]:.0f} dB above its surroundings,"
            f" {n_lines} of 3 harmonics above 8 dB"
        )
    else:
        summary = f"no line at 50 or 60 Hz (strongest {max(table[50][0], table[60][0]):.0f} dB)"
    evidence = {
        "nominal_hz": nominal if n_lines else None,
        "lines_db": {"50": table[50], "60": table[60]},
    }
    return Clue(
        "mains_hum", label, n_lines > 0, confidence, summary, evidence, HUM_LEAKS, HUM_COUNTER
    )


def metadata_clue(metadata: dict[str, object]) -> Clue:
    """Location or creation-time tags in the container."""
    label = "metadata"
    tags = metadata.get("tags")
    tags = tags if isinstance(tags, dict) else {}
    location = [k for k in tags if any(w in k.lower() for w in LOCATION_KEYWORDS)]
    when = [k for k in tags if any(w in k.lower() for w in TIME_KEYWORDS)]
    if not metadata.get("available"):
        return _absent(
            "metadata", label, "not checked (ffprobe not available)", METADATA_LEAKS,
            METADATA_COUNTER,
        )  # fmt: skip
    if location:
        confidence, summary = "likely", f"location tag {location[0]} = {tags[location[0]]}"
    elif when:
        confidence, summary = "weak", f"time tag {when[0]} = {tags[when[0]]}"
    else:
        confidence, summary = "none", "no location or time tags"
    evidence = {"location_tags": location, "time_tags": when, "tags": tags}
    return Clue(
        "metadata", label, confidence != "none", confidence, summary, evidence, METADATA_LEAKS,
        METADATA_COUNTER,
    )  # fmt: skip


def probe_metadata(path: Path) -> dict[str, object]:
    """Container and stream facts plus the tags that could matter, via ffprobe when available."""
    exe = shutil.which("ffprobe")
    if exe is None:
        ff = ffmpeg_path()
        if ff:
            sibling = Path(ff).with_name("ffprobe" + Path(ff).suffix)
            exe = str(sibling) if sibling.exists() else None
    if exe is None:
        return {"available": False, "tags": {}}
    cmd = [exe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        info = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"available": False, "tags": {}}
    fmt = info.get("format", {}) if isinstance(info, dict) else {}
    streams = info.get("streams", []) if isinstance(info, dict) else []
    tags: dict[str, str] = {}
    for source in [fmt.get("tags", {})] + [s.get("tags", {}) for s in streams]:
        if isinstance(source, dict):
            tags.update({str(k): str(v) for k, v in source.items()})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    interesting = {k: v for k, v in tags.items() if any(w in k.lower() for w in METADATA_KEYWORDS)}
    rate = audio.get("sample_rate")
    return {
        "available": True,
        "container": fmt.get("format_name"),
        "codec": audio.get("codec_name"),
        "sample_rate_hz": int(rate) if isinstance(rate, str) and rate.isdigit() else None,
        "channels": audio.get("channels"),
        "has_video": any(s.get("codec_type") == "video" for s in streams),
        "bit_rate": fmt.get("bit_rate"),
        "tags": interesting,
    }


def audit_samples(samples: FloatArray, rate: int, metadata: dict[str, object]) -> list[Clue]:
    """All clue detectors on already decoded mono samples."""
    return [
        metadata_clue(metadata),
        thunder_clue(samples, rate),
        aircraft_clue(samples, rate),
        mains_hum_clue(samples, rate),
    ]


def audit_file(
    path: str | Path, samples: FloatArray | None = None, rate: int | None = None
) -> AuditReport:
    """Audit a local file; pass decoded samples to skip decoding it again."""
    p = Path(path)
    if samples is None or rate is None:
        samples, rate = read_audio(p, target_rate=AUDIT_RATE_HZ)
    metadata = probe_metadata(p)
    file_info: dict[str, object] = {
        "name": p.name,
        "path": str(p.resolve()),
        "size_bytes": p.stat().st_size,
        "sha256": sha256_file(p),
        "duration_s": samples.size / rate,
        "rate_hz": rate,
    }
    return AuditReport(SCHEMA, file_info, metadata, audit_samples(samples, rate, metadata))
