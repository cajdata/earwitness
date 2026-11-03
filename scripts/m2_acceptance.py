"""M2 acceptance harness: synthetic thunder cases built from real GLM storm data.

For each case the harness picks a land site where at least `--min-strikes` GLM flashes fell
within 25 km during some clip-length window, renders a synthetic recording from those real
flashes, hides the truth from the search, runs the full thunder pipeline over the whole region
and time range, and scores the result: distance of the rank-1 answer from the truth, the rank of
the first answer within 5 km, the clip-start error, and the runtime.

Needs network access the first time (GLM hours and the Natural Earth land mask are cached).

Example:
  uv run python scripts/m2_acceptance.py --date 2025-05-16 --hours 21-23 \\
      --bbox -100,30,-85,42 --cases 10 --seed 1 --out runs/m2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from earwitness.core import geodesy
from earwitness.core.grid import BBox, grid_points, parse_bbox
from earwitness.data.adapters import LightningEvents
from earwitness.data.glm import load_flashes, resolve_satellite
from earwitness.data.landmask import LandMask
from earwitness.project import parse_utc_date, utc_iso
from earwitness.simulate import MixOptions, write_thunder_case
from earwitness.simulate.thunder_synth import Strike, ThunderScenario
from earwitness.thunder.pipeline import PipelineConfig, ThunderPriors, analyze_thunder
from earwitness.thunder.search import (
    SearchConfig,
    StrikeIndex,
    strike_count_prefilter,
)

R_MAX_M = 25000.0


def pick_sites(
    events: LightningEvents,
    bbox: BBox,
    landmask: LandMask,
    rng: np.random.Generator,
    n_cases: int,
    clip_s: float,
    min_strikes: int,
    min_separation_m: float,
) -> list[tuple[float, float, int]]:
    """Random land sites that heard at least `min_strikes` flashes in some clip-length window."""
    inner = bbox.expand(-60000.0)
    lat, lon = grid_points(inner, 10000.0)
    keep = landmask.contains(lat, lon)
    lat, lon = lat[keep], lon[keep]
    index = StrikeIndex(events)
    counts = strike_count_prefilter(index, lat, lon, R_MAX_M, clip_s)
    ok = np.flatnonzero(counts >= min_strikes)
    rng.shuffle(ok)
    chosen: list[tuple[float, float, int]] = []
    for i in ok:
        if chosen:
            d = geodesy.horizontal_distance(
                [c[0] for c in chosen], [c[1] for c in chosen], float(lat[i]), float(lon[i])
            )
            if np.min(d) < min_separation_m:
                continue
        chosen.append((float(lat[i]), float(lon[i]), int(counts[i])))
        if len(chosen) == n_cases:
            break
    return chosen


def busiest_window(
    events: LightningEvents, lat: float, lon: float, clip_s: float, start: float, end: float
) -> float:
    """Unix start, inside [start, end], of the busiest clip-length window at a site."""
    d = geodesy.horizontal_distance(events.lat, events.lon, lat, lon)
    t = np.sort(events.time_unix[d <= R_MAX_M])
    starts = t[(t >= start) & (t <= end)]
    if starts.size == 0:
        return start
    counts = np.searchsorted(t, starts + clip_s, side="right") - np.searchsorted(
        t, starts, side="left"
    )
    return float(starts[int(np.argmax(counts))])


def fmt(value: object, spec: str) -> str:
    """Format a number, or 'n/a' when missing."""
    return "n/a" if value is None else format(value, spec)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--date", required=True)
    ap.add_argument("--hours", default="21-23", help="UTC hour range (default 21-23)")
    ap.add_argument(
        "--bbox", required=True, nargs="+", help="region: west south east north (or --bbox=w,s,e,n)"
    )
    ap.add_argument("--cases", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--snr-db", type=float, default=10.0)
    ap.add_argument("--sample-rate", type=int, default=8000)
    ap.add_argument("--min-strikes", type=int, default=12, help="flashes within 25 km at a site")
    ap.add_argument("--satellite", default="auto")
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--grid-km", type=float, default=5.0)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument(
        "--synth-radius-km",
        type=float,
        default=25.0,
        help="synthesize from strikes within this distance (default 25, the model radius)",
    )
    ap.add_argument("--score", choices=["cosine", "pearson"], default="cosine")
    ap.add_argument("--bearing-weight", type=float, default=0.0)
    ap.add_argument(
        "--conus-day",
        action="store_true",
        help="one case over the full day and the whole region, for the runtime target",
    )
    args = ap.parse_args(argv)

    bbox = parse_bbox(args.bbox)
    h0, h1 = (int(p) for p in args.hours.split("-"))
    day = parse_utc_date(args.date)
    start, end = day + timedelta(hours=h0), day + timedelta(hours=h1 + 1)
    if args.conus_day:
        start, end = day, day + timedelta(days=1)
    satellite = resolve_satellite(args.satellite, day.date(), bbox)
    print(f"loading GLM {satellite} {utc_iso(start)} .. {utc_iso(end)} in {bbox}")
    events = load_flashes(
        satellite,
        start - timedelta(hours=1),
        end + timedelta(hours=1),
        bbox.expand(30000.0),
        args.cache,
        log=print,
    )
    print(f"{len(events)} flashes")
    landmask = LandMask.natural_earth(args.cache, "50m")
    rng = np.random.default_rng(args.seed)
    clip_s = args.minutes * 60.0
    n_cases = 1 if args.conus_day else args.cases
    in_range = events.between(start.timestamp(), end.timestamp())
    sites = pick_sites(in_range, bbox, landmask, rng, n_cases, clip_s, args.min_strikes, 50000.0)
    if len(sites) < n_cases:
        print(f"only {len(sites)} usable sites found", file=sys.stderr)
    args.out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for k, (lat, lon, count) in enumerate(sites, start=1):
        case_dir = args.out / f"case_{k:02d}"
        t_clip = busiest_window(events, lat, lon, clip_s, start.timestamp(), end.timestamp()) - 60.0
        t_clip = min(max(t_clip, start.timestamp()), end.timestamp())
        clip_start = datetime.fromtimestamp(t_clip, tz=UTC)
        near = events.subset(
            geodesy.horizontal_distance(events.lat, events.lon, lat, lon)
            <= args.synth_radius_km * 1000.0
        )
        near = near.between(t_clip - 120.0, t_clip + clip_s)
        strikes = [
            Strike(float(t - t_clip), float(la), float(lo))
            for t, la, lo in zip(near.time_unix, near.lat, near.lon, strict=True)
        ]
        scenario = ThunderScenario(lat, lon, 0.0, clip_start, clip_s, strikes)
        write_thunder_case(
            case_dir / "truth",
            scenario,
            args.sample_rate,
            MixOptions(snr_db=args.snr_db),
            args.seed + k,
            np.random.default_rng(args.seed + k),
        )
        print(
            f"\ncase {k}: site {lat:.4f},{lon:.4f} with {count} flashes in a window; "
            f"clip {utc_iso(clip_start)}; {len(strikes)} strikes within {args.synth_radius_km:g} km"
        )
        cfg = PipelineConfig(
            search=SearchConfig(bearing_weight=args.bearing_weight, score_mode=args.score),
            grid_spacing_m=args.grid_km * 1000.0,
            top_k=args.top,
        )
        priors = ThunderPriors(start, end, bbox)
        t0 = time.perf_counter()
        summary = analyze_thunder(
            case_dir / "truth" / "recording.wav",
            events,
            priors,
            case_dir / "run",
            cfg,
            landmask,
            progress=None,
        )
        runtime = time.perf_counter() - t0
        top = summary["top"]
        dists = [float(geodesy.horizontal_distance(t["lat"], t["lon"], lat, lon)) for t in top]
        rank_within = next(
            (t["rank"] for t, d in zip(top, dists, strict=True) if d <= 5000.0), None
        )
        row: dict[str, object] = {
            "case": k,
            "truth_lat": lat,
            "truth_lon": lon,
            "clip_start_utc": utc_iso(clip_start),
            "strikes_in_window": count,
            "top1_error_m": dists[0] if dists else None,
            "top1_offset_error_s": (top[0]["offset_unix"] - t_clip) if top else None,
            "top1_score": top[0]["score"] if top else None,
            "runner_up_score": top[1]["score"] if len(top) > 1 else None,
            "rank_within_5km": rank_within,
            "candidates": summary["counts"]["prefilter"],
            "runtime_s": runtime,
        }
        rows.append(row)
        print(
            f"  rank-1 error {fmt(row['top1_error_m'], '.0f')} m, "
            f"offset error {fmt(row['top1_offset_error_s'], '+.1f')} s, "
            f"score {fmt(row['top1_score'], '.3f')} vs {fmt(row['runner_up_score'], '.3f')}, "
            f"rank within 5 km: {rank_within}, {row['candidates']} candidates, {runtime:.0f} s"
        )

    passed = sum(1 for r in rows if r["rank_within_5km"] == 1)
    report = {
        "date": args.date,
        "hours": args.hours,
        "bbox": str(bbox),
        "satellite": satellite,
        "flashes": len(events),
        "cases": rows,
        "passed_rank1_within_5km": passed,
        "total": len(rows),
    }
    (args.out / "m2_acceptance.json").write_text(json.dumps(report, indent=2))
    header = (
        "| case | strikes in window | rank-1 error (m) | offset error (s) | score | "
        "runner-up | rank within 5 km | candidates | runtime (s) |"
    )
    lines = [
        f"# M2 acceptance: {args.date} hours {args.hours}, {satellite}, region {bbox}",
        "",
        header,
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['case']} | {r['strikes_in_window']} | {fmt(r['top1_error_m'], '.0f')} | "
            f"{fmt(r['top1_offset_error_s'], '+.1f')} | {fmt(r['top1_score'], '.3f')} | "
            f"{fmt(r['runner_up_score'], '.3f')} | {r['rank_within_5km']} | "
            f"{r['candidates']} | {fmt(r['runtime_s'], '.0f')} |"
        )
    lines += ["", f"Rank 1 within 5 km: {passed} of {len(rows)}."]
    (args.out / "m2_acceptance.md").write_text("\n".join(lines) + "\n")
    print(f"\nrank 1 within 5 km: {passed} of {len(rows)}; report in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
