"""M6 clip sweep: how short a clip can be, measured on a real recording with a known clock.

For each clip length, the recording is cut to an excerpt centered on a measured flyover (or on
the midpoint of two), every event the mode needs is measured in the excerpt alone and fitted,
and the excerpt is searched with the clock: the recording's known start plus the excerpt's
offset. The measurement is either the analyst's own trace from an events file, cut to the
excerpt (`--trace-from`, what a careful user traces in that clip, so the sweep measures what the
clip holds), or a fresh auto-trace in the excerpt (`--event`, the UI's route through
`m6_measure.trace` with the event's band and window cut to the excerpt).
Modes per length: each event alone with the clock (single-event known-time) and, when the
excerpt holds both, the first two events with the clock. The day's flights and the terrain load
once; every run gets its own folder with the pipeline's run log and events.json, and is scored
against the private truth with its start shifted by the excerpt offset (`m6_score.score_run`).
A run whose events cannot be traced in the excerpt (fewer than five trace points) is reported
as not measurable, which is a result too: the clip was too short to hold the line.

Usage, from the repo root (cut traces; or --event id,t_lo,t_hi,f_lo,f_hi[,h] to re-trace):
  uv run python scripts/m6_clip_sweep.py <audio> --truth <truth.json> --date 2026-11-04
      --bbox W,S,E,N --known-time 2026-11-04T21:37:51Z --tolerance 2
      --trace-from data/m6/personal-1/events_two2.json
      --lengths 600,300,180,120,60,30 --out runs/m6-personal-1-clips
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m6_measure import parse_event, trace
from m6_score import MARKDOWN_HEADER, markdown_row, score_run

from earwitness.aircraft.fit import fit_straight_flyover
from earwitness.aircraft.funnel import FunnelConfig
from earwitness.aircraft.pipeline import (
    AircraftPipelineConfig,
    AircraftPriors,
    analyze_aircraft,
)
from earwitness.aircraft.prep import Flight, prepare_flights
from earwitness.aircraft.score import ScoreConfig
from earwitness.audio.decode import read_audio, write_wav
from earwitness.audio.events import EventSet, FlyoverEvent, Range
from earwitness.audio.spectrogram import spectrogram
from earwitness.core.grid import BBox, parse_bbox
from earwitness.data.adsb_lol import cached_regions, load_day
from earwitness.data.aircraft_types import TypeTable
from earwitness.project import parse_utc, parse_utc_date, read_json, utc_iso, write_json

FloatArray = NDArray[np.float64]
RATE = 4000
"""Excerpts are written and measured at the pipeline's own spectrogram rate."""
MIN_MARGIN_S = 10.0
"""A two-event excerpt keeps at least this much audio beyond each event's heard crossing."""


@dataclass(frozen=True)
class EventSpec:
    """An event's trace window and band in the full recording, or its stored trace."""

    id: str
    t_lo: float
    t_hi: float
    f_lo: float
    f_hi: float
    harmonics: int
    trace: FloatArray | None = None
    """(n, 2) [t_s, f_hz] in recording time; when given, excerpts cut it instead of tracing."""


def measure(
    clip: FloatArray, spec: EventSpec, offset_s: float
) -> tuple[FlyoverEvent | None, dict[str, object]]:
    """Trace and fit one event inside an excerpt that starts `offset_s` into the recording."""
    duration = clip.size / RATE
    t_lo = max(spec.t_lo - offset_s, 0.0)
    t_hi = min(spec.t_hi - offset_s, duration)
    info: dict[str, object] = {"id": spec.id, "window_s": [t_lo, t_hi]}
    if spec.trace is not None:
        rel = spec.trace[:, 0] - offset_s
        inside = (rel >= 0.0) & (rel <= duration)
        t, f = rel[inside], spec.trace[inside, 1]
    elif t_hi - t_lo < 2.0:
        info["points"] = 0
        return None, info
    else:
        sg = spectrogram(clip, RATE, nfft=4096, hop=512, fmax=450.0)
        t, f, _ = trace(sg, spec.f_lo, spec.f_hi, t_lo, t_hi)
    info["points"] = int(t.size)
    if t.size < 5:
        return None, info
    fit = fit_straight_flyover(t, f)
    r = fit.ranges()
    rows = np.asarray(fit.heard_valley(), dtype=float)
    info.update(
        {
            "trace_s": [float(t.min()), float(t.max())],
            "fit": {
                "f0_hz": fit.f0_hz,
                "v_mps": fit.v_mps,
                "d_m": fit.d_m,
                "heard_t0_s": fit.heard_t0_s(),
                "rms_hz": fit.rms_hz,
            },
            "valley_v_mps": [float(rows[:, 0].min()), float(rows[:, 0].max())],
            "ranges": r,
        }
    )
    event = FlyoverEvent(
        id=spec.id,
        t0_s=Range(*r["t0_s"]),
        f0_hz=Range(*r["f0_hz"]),
        v_mps=Range(*r["v_mps"]),
        d_m=Range(*r["d_m"]),
        harmonics=spec.harmonics,
        trace=np.column_stack([t, f]),
        valley=rows[:, :4],
    )
    return event, info


def load_flights(day_text: str, region: BBox, cache: Path | None, variant: str) -> list[Flight]:
    """The cached adsb.lol day for the region, prepared once for every run."""
    day = parse_utc_date(day_text).date()
    options = [(b, p) for b, p in cached_regions(cache, day, variant) if b.contains(*region.center)]
    if not options:
        raise SystemExit(f"no cached adsb.lol day covering the region for {day}")
    table = load_day(day, options[-1][0], cache, variant, sub_bbox=region.expand(12000.0))
    flights = prepare_flights(table, TypeTable.load(cache))
    del table
    return flights


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("audio", type=Path)
    ap.add_argument("--truth", type=Path, required=True, help="private truth JSON with start_utc")
    ap.add_argument("--date", required=True, help="UTC date of the recording, YYYY-MM-DD")
    ap.add_argument("--bbox", required=True, nargs="+", help="search region west south east north")
    ap.add_argument("--known-time", required=True, help="recording start by its clock, UTC ISO")
    ap.add_argument("--tolerance", type=float, default=2.0, help="clock tolerance, s")
    ap.add_argument(
        "--event", action="append", default=[], help="id,t_lo,t_hi,f_lo,f_hi[,harmonics]: re-trace"
    )
    ap.add_argument(
        "--trace-from",
        type=Path,
        default=None,
        help="events.json whose stored traces are cut to each excerpt (instead of --event)",
    )
    ap.add_argument("--lengths", required=True, help="clip lengths in seconds, comma separated")
    ap.add_argument(
        "--modes",
        default="single,two",
        help="single (each event alone), two (first two events), or both (default)",
    )
    ap.add_argument(
        "--two-margin",
        type=float,
        default=MIN_MARGIN_S,
        help="two-event excerpts keep this much beyond each heard crossing, s",
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--variant", default="prod")
    ap.add_argument("--silence", choices=["hard", "soft", "off"], default="soft")
    ap.add_argument("--no-terrain", action="store_true")
    ap.add_argument(
        "--trace-window", action="store_true", help="score over each trace's span (an option)"
    )
    args = ap.parse_args(argv)

    if args.trace_from is not None:
        stored = EventSet.load(args.trace_from).events
        specs = [
            EventSpec(e.id, 0.0, 0.0, 0.0, 0.0, e.harmonics, e.trace)
            for e in stored
            if e.trace is not None and e.trace.shape[0] >= 5
        ]
    else:
        specs = [EventSpec(*parse_event(raw)) for raw in args.event]
    if not specs:
        raise SystemExit("give --event windows or --trace-from an events file with traces")
    lengths = sorted({float(x) for x in args.lengths.split(",")}, reverse=True)
    modes = {m.strip() for m in args.modes.split(",")}
    region = parse_bbox(args.bbox)
    known = parse_utc(args.known_time)
    truth = read_json(args.truth)
    if not isinstance(truth, dict) or "start_utc" not in truth:
        raise SystemExit("the truth file needs start_utc for a known-time sweep")
    truth_start = parse_utc(str(truth["start_utc"]))
    args.out.mkdir(parents=True, exist_ok=True)

    samples, rate = read_audio(args.audio, target_rate=RATE)
    duration = samples.size / rate
    print(f"{args.audio.name}: {duration:.1f} s at {rate} Hz", flush=True)
    centers: dict[str, float] = {}
    for spec in specs:
        event, info = measure(samples, spec, 0.0)
        if event is None:
            raise SystemExit(f"event {spec.id} cannot be traced in the full recording")
        fit = info["fit"]  # type: ignore[assignment]
        centers[spec.id] = float(fit["heard_t0_s"])  # type: ignore[index]
        print(
            f"full recording, {spec.id}: {info['points']} points, fit "
            f"{fit['f0_hz']:.1f} Hz, {fit['v_mps']:.0f} m/s, {fit['d_m']:.0f} m, heard "  # type: ignore[index]
            f"{fit['heard_t0_s']:.1f} s, rms {fit['rms_hz']:.2f} Hz",  # type: ignore[index]
            flush=True,
        )

    t0 = time.perf_counter()
    flights = load_flights(args.date, region, args.cache, args.variant)
    print(f"{len(flights)} legs loaded in {time.perf_counter() - t0:.0f} s", flush=True)
    terrain = None
    if not args.no_terrain:
        from earwitness.data.dem import Terrain

        terrain = Terrain(args.cache)
    day = parse_utc_date(args.date)
    priors = AircraftPriors(day, day + timedelta(days=1), region)
    cfg = AircraftPipelineConfig(
        funnel=FunnelConfig(silence_mode=args.silence),
        score=ScoreConfig(trace_window=args.trace_window),
        use_terrain=not args.no_terrain,
    )

    plan: list[tuple[str, list[EventSpec], float]] = []
    for length in lengths:
        if "single" in modes:
            plan += [(f"single-{s.id}", [s], length) for s in specs]
        if "two" in modes and len(specs) >= 2:
            gap = abs(centers[specs[1].id] - centers[specs[0].id])
            if length >= min(gap + 2.0 * args.two_margin, duration):
                plan.append(("two", specs[:2], length))

    rows: list[dict[str, object]] = []
    md = [MARKDOWN_HEADER]
    for mode, needed, length in plan:
        span = min(length, duration)
        center = float(np.mean([centers[s.id] for s in needed]))
        start = float(np.clip(center - span / 2.0, 0.0, duration - span))
        name = f"{mode}-{span:.0f}s"
        run_dir = args.out / name
        clip = samples[round(start * rate) : round((start + span) * rate)]
        clip_path = write_wav(run_dir / "clip.wav", clip, rate, subtype="FLOAT")
        events: list[FlyoverEvent] = []
        infos: list[dict[str, object]] = []
        for spec in needed:
            event, info = measure(clip, spec, start)
            infos.append(info)
            if event is not None:
                events.append(event)
        row: dict[str, object] = {
            "name": name,
            "mode": mode,
            "length_s": span,
            "start_s": start,
            "measured": infos,
        }
        if len(events) < len(needed):
            row["result"] = "not measurable"
            print(f"\n{name}: not measurable ({infos})", flush=True)
            rows.append(row)
            continue
        es = EventSet(
            events=events,
            known_time=known + timedelta(seconds=start),
            known_time_tolerance_s=args.tolerance,
            recording={"file": clip_path.name},
        )
        es.save(run_dir / "events.json")
        t_run = time.perf_counter()
        summary = analyze_aircraft(clip_path, es, flights, priors, run_dir / "run", cfg, terrain)
        clip_truth = dict(truth)
        clip_truth["start_utc"] = utc_iso(truth_start + timedelta(seconds=start), True)
        truth_path = args.truth.parent / "clips" / f"{args.out.name}-{name}.json"
        write_json(truth_path, clip_truth)
        scored, tol = score_run(run_dir / "run", truth_path, name, None)
        top: dict[str, object] = scored["top"]  # type: ignore[assignment]
        row.update(
            {
                "result": "run",
                "runtime_s": time.perf_counter() - t_run,
                "counts": summary["counts"],
                "rank1_error_m": top.get("rank1_error_m"),
                "rank_within": top.get("rank_within"),
                "rank1_score": top.get("rank1_score"),
                "margin": top.get("margin"),
                "start_error_s": top.get("start_error_s"),
                "stages": scored["stages"],
            }
        )
        rows.append(row)
        md.append(markdown_row(scored, tol))
        fits = "; ".join(
            f"{i['id']} {i['points']} pts, v {i['valley_v_mps'][0]:.0f}-"  # type: ignore[index]
            f"{i['valley_v_mps'][1]:.0f} m/s"  # type: ignore[index]
            for i in infos
        )
        print(
            f"\n{name} (start {start:.0f} s): {fits}; counts {summary['counts']}; rank-1 "
            f"error {top.get('rank1_error_m') or float('nan'):.0f} m, rank within {tol:g} m "
            f"{top.get('rank_within')}, margin {top.get('margin')}, "
            f"{row['runtime_s']:.0f} s",
            flush=True,
        )
        write_json(args.out / "sweep.json", rows)
        (args.out / "sweep.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    write_json(args.out / "sweep.json", rows)
    (args.out / "sweep.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nwrote {args.out / 'sweep.json'} and sweep.md", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
