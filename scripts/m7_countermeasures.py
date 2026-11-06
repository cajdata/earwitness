"""M7: which edits stop the two methods, measured on the demo's synthetic cases.

Each variant edits the demo's thunder or aircraft recording the way a person might before
sharing it (a low-cut filter with the level restored, lossy compression, cuts) and runs the
same search as `earwitness demo`. Aircraft events are the truth's ranges, shifted for cuts the
way an analyst measuring the edited file would see them. One synthetic case per method: a
check of the mechanisms behind docs/COUNTERMEASURES.md, not a survey of real recordings.

  uv run python scripts/m7_countermeasures.py --out runs/m7-countermeasures

Needs ffmpeg on PATH for the AAC and MP3 variants (they are skipped without it). Writes one run
folder per variant and results.json, and prints the table for docs/COUNTERMEASURES.md.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

import numpy as np

from earwitness.aircraft.funnel import FunnelConfig
from earwitness.aircraft.pipeline import AircraftPipelineConfig, AircraftPriors, analyze_aircraft
from earwitness.aircraft.score import ScoreConfig
from earwitness.audio.decode import ffmpeg_path, read_audio, write_wav
from earwitness.audio.events import EventSet, Range, SilenceWindow, events_from_truth
from earwitness.core import geodesy
from earwitness.core.grid import BBox
from earwitness.data.adapters import load_events
from earwitness.demo import (
    AIRCRAFT_START,
    AIRCRAFT_TOLERANCE_M,
    DEMO_LAT,
    DEMO_LON,
    THUNDER_START,
    THUNDER_TOLERANCE_M,
    with_decoys,
    write_aircraft_demo_case,
    write_thunder_demo_case,
)
from earwitness.simulate.mix import codec_roundtrip, highpass, normalize_peak
from earwitness.thunder.pipeline import PipelineConfig, ThunderPriors, analyze_thunder

RATE = 16000  # the codecs need a standard rate; the demo itself renders at 4 kHz
Edit = Callable[[np.ndarray, int], np.ndarray]


@dataclass
class Variant:
    """One edit of a recording and how the search is then run."""

    name: str
    edit: Edit | None = None
    cuts: tuple[tuple[float, float], ...] = ()
    """(start, length) in seconds of the original recording, removed in order."""
    clock_s: float | None = None
    """Known start, plus or minus this many seconds; None for no clock."""
    harmonics: int = 1
    first_event_only: bool = False
    silence: str = "hard"
    """The silence filter: hard drops a candidate that would have heard another flight while
    the recording was silent, soft costs it 3 dB per such flight."""
    needs_ffmpeg: bool = False


def lowcut(cutoff_hz: float, order: int) -> Edit:
    """A recorder's low-cut filter, then the level brought back up, as an editor would."""
    return lambda x, rate: highpass(x, rate, cutoff_hz, order)


def codec(name: str, kbps: int) -> Edit:
    """A lossy round trip through ffmpeg."""
    return lambda x, rate: codec_roundtrip(x, rate, name, kbps)


def apply_cuts(x: np.ndarray, rate: int, cuts: tuple[tuple[float, float], ...]) -> np.ndarray:
    """Remove each (start, length) span of the original timeline."""
    keep = np.ones(x.size, dtype=bool)
    for start, length in cuts:
        keep[int(start * rate) : int((start + length) * rate)] = False
    return x[keep]


def shift_time(t: float, cuts: tuple[tuple[float, float], ...]) -> float | None:
    """Where an original recording time lands after the cuts, or None if it was cut out."""
    out = t
    for start, length in cuts:
        if start <= t < start + length:
            return None
        if t >= start + length:
            out -= length
    return out


def edited_events(events: EventSet, v: Variant) -> EventSet:
    """The truth's event ranges as an analyst measuring the edited file would set them."""
    kept = []
    for e in events.events:
        mid = shift_time(e.t0_s.mid, v.cuts)
        if mid is None:
            continue
        shifted = Range(mid - e.t0_s.half_width, mid + e.t0_s.half_width)
        kept.append(replace(e, t0_s=shifted, harmonics=v.harmonics))
    if v.first_event_only:
        kept = kept[:1]
    silence = []
    for w in events.silence:
        a, b = shift_time(w.start_s, v.cuts), shift_time(w.end_s - 1e-6, v.cuts)
        if a is not None and b is not None and b - a >= 30.0:
            silence.append(SilenceWindow(a, b))
    out = EventSet(events=kept, silence=silence, recording=events.recording)
    if v.clock_s is not None:
        out.known_time = AIRCRAFT_START
        out.known_time_tolerance_s = v.clock_s
    return out


def distance(t: dict[str, object]) -> float:
    return float(geodesy.horizontal_distance(t["lat"], t["lon"], DEMO_LAT, DEMO_LON))  # type: ignore[arg-type]


def summarize(summary: dict[str, object], tolerance_m: float, method: str) -> dict[str, object]:
    """Rank-1 error, first rank within tolerance, score and margin over the next answer (the
    best other flight pair for aircraft, the next region for thunder)."""
    top = list(summary["top"])  # type: ignore[call-overload]
    if not top:
        return {"error_m": None, "rank_within": None, "score": None, "margin": None}
    within = [int(t["rank"]) for t in top if distance(t) <= tolerance_m]
    best = top[0]
    if method == "aircraft":
        pair = {f["icao"] for f in best["flights"] if f}
        others = [t["score"] for t in top[1:] if {f["icao"] for f in t["flights"] if f} != pair]
    else:
        others = [t["score"] for t in top[1:]]
    margin = float(best["score"]) - float(others[0]) if others else None
    return {
        "error_m": distance(best),
        "rank_within": within[0] if within else None,
        "score": float(best["score"]),
        "margin": margin,
    }


def write_variant(base: Path, v: Variant, out: Path) -> Path:
    x, rate = read_audio(base)
    if v.edit is not None:
        x = v.edit(x, rate)
    x = apply_cuts(x, rate, v.cuts)
    return write_wav(out, normalize_peak(x, 0.5), rate)


def run_thunder(base_dir: Path, out: Path, v: Variant) -> dict[str, object]:
    wav = write_variant(base_dir / "recording.wav", v, out / "cases" / f"thunder-{v.name}.wav")
    if v.clock_s is None:
        start = THUNDER_START - timedelta(minutes=30)
        end = THUNDER_START + timedelta(minutes=30)
    else:
        start = THUNDER_START - timedelta(seconds=v.clock_s)
        end = THUNDER_START + timedelta(seconds=v.clock_s)
    priors = ThunderPriors(start, end, BBox.around(DEMO_LAT, DEMO_LON, 60000.0))
    cfg = PipelineConfig(top_k=5, use_land_mask=False, overlay_count=1)
    events = load_events(base_dir / "strikes.json")
    summary = analyze_thunder(wav, events, priors, out / "runs" / f"thunder-{v.name}", cfg, None)
    return summarize(summary, THUNDER_TOLERANCE_M, "thunder")


def run_aircraft(
    base_dir: Path, truth: dict[str, object], out: Path, v: Variant
) -> dict[str, object]:
    wav = write_variant(base_dir / "recording.wav", v, out / "cases" / f"aircraft-{v.name}.wav")
    events = edited_events(events_from_truth(truth), v)
    priors = AircraftPriors(
        AIRCRAFT_START - timedelta(minutes=10),
        AIRCRAFT_START + timedelta(minutes=10),
        BBox.around(DEMO_LAT, DEMO_LON, 15000.0),
    )
    cfg = AircraftPipelineConfig(
        funnel=FunnelConfig(silence_mode=v.silence),
        score=ScoreConfig(harmonics=v.harmonics),
        use_terrain=False,
        top_pairs=5,
        heatmap_pairs=1,
    )
    flights = with_decoys(base_dir / "tracks.json")
    run = out / "runs" / f"aircraft-{v.name}"
    summary = analyze_aircraft(wav, events, flights, priors, run, cfg)  # type: ignore[arg-type]
    return summarize(summary, AIRCRAFT_TOLERANCE_M, "aircraft")


THUNDER = [
    Variant("as-recorded"),
    Variant("lowcut-150hz", edit=lowcut(150.0, 2)),
    Variant("aac-64k", edit=codec("aac", 64), needs_ffmpeg=True),
    Variant("mp3-16k", edit=codec("mp3", 16), needs_ffmpeg=True),
    Variant("one-60s-cut", cuts=((450.0, 60.0),)),
    Variant("three-20s-cuts", cuts=((240.0, 20.0), (480.0, 20.0), (720.0, 20.0))),
    Variant("one-60s-cut-clock-5s", cuts=((450.0, 60.0),), clock_s=5.0),
]
AIRCRAFT = [
    Variant("as-recorded"),
    Variant("lowcut-300hz", edit=lowcut(300.0, 2)),
    Variant("steep-lowcut-300hz", edit=lowcut(300.0, 8)),
    Variant("steep-lowcut-300hz-4-harmonics", edit=lowcut(300.0, 8), harmonics=4),
    Variant("aac-64k", edit=codec("aac", 64), needs_ffmpeg=True),
    Variant("mp3-16k", edit=codec("mp3", 16), needs_ffmpeg=True),
    Variant("60s-cut-between", cuts=((400.0, 60.0),)),
    Variant(
        "60s-cut-between-first-alone-clock-2s",
        cuts=((400.0, 60.0),),
        clock_s=2.0,
        first_event_only=True,
    ),
    Variant(
        "60s-cut-between-first-alone-clock-2s-soft-silence",
        cuts=((400.0, 60.0),),
        clock_s=2.0,
        first_event_only=True,
        silence="soft",
    ),
]


def fmt(r: dict[str, object]) -> str:
    if r.get("skipped"):
        return "skipped (no ffmpeg) | | |"
    err = r["error_m"]
    if err is None:
        return "no answer | none | |"
    where = f"{err / 1000:.1f} km" if err >= 1000 else f"{err:.0f} m"
    rank = r["rank_within"] if r["rank_within"] is not None else "none"
    margin = "" if r["margin"] is None else f"{r['margin']:.3f}"
    return f"{where} | {rank} | {r['score']:.3f} | {margin}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("runs/m7-countermeasures"))
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    have_ffmpeg = ffmpeg_path() is not None
    write_thunder_demo_case(out / "base" / "thunder", RATE)
    truth = write_aircraft_demo_case(out / "base" / "aircraft", RATE)
    rows = []
    for method, variants in (("thunder", THUNDER), ("aircraft", AIRCRAFT)):
        for v in variants:
            t0 = time.perf_counter()
            if v.needs_ffmpeg and not have_ffmpeg:
                r: dict[str, object] = {"skipped": True}
            elif method == "thunder":
                r = run_thunder(out / "base" / "thunder", out, v)
            else:
                r = run_aircraft(out / "base" / "aircraft", truth, out, v)
            r.update(method=method, variant=v.name, runtime_s=time.perf_counter() - t0)
            rows.append(r)
            print(f"| {method} | {v.name} | {fmt(r)} |", flush=True)
    (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
