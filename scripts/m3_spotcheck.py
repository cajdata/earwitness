"""M3 acceptance: summarize a cached adsb.lol day and spot-check flights against adsb.lol itself.

For a sample of aircraft in the cached day, the script fetches adsb.lol's own per-day trace
file (the data its replay view loads), parses it with the same parser, clips it to the same
region and day, and compares every kept point with the cache: same count, same times,
positions and altitudes. It also reports the day's size, the engine-class breakdown of the
prepared flights, and how many type designators the type table could not classify.

Example:
  uv run python scripts/m3_spotcheck.py --date 2026-09-08 --bbox=-125,24,-66,50 --sample 8
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow.compute as pc

from earwitness.aircraft.prep import flights_summary, prepare_flights
from earwitness.core.grid import parse_bbox
from earwitness.data.adsb_lol import clip_trace, history_trace_url, load_day, parse_trace
from earwitness.data.aircraft_types import TypeTable
from earwitness.data.cache import cache_root, download
from earwitness.project import parse_utc_date


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--date", required=True)
    ap.add_argument(
        "--bbox", required=True, nargs="+", help="west south east north (or --bbox=w,s,e,n)"
    )
    ap.add_argument("--variant", default="prod")
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--sample", type=int, default=8, help="aircraft to compare with adsb.lol")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None, help="write a JSON report here")
    args = ap.parse_args(argv)

    bbox = parse_bbox(args.bbox)
    day = parse_utc_date(args.date).date()
    root = cache_root(args.cache)
    table = load_day(day, bbox, root, args.variant)
    icaos = table.column("icao").to_numpy(zero_copy_only=False)
    uniq, counts = np.unique(icaos, return_counts=True)
    print(f"{table.num_rows} points, {uniq.size} aircraft in {bbox} on {day}")

    types = TypeTable.load(root)
    flights = prepare_flights(table, types)
    summary = flights_summary(flights)
    codes = Counter(f.type_code for f in flights)
    unknown = Counter(f.type_code for f in flights if f.engine_class == "unknown")
    print(
        f"{len(flights)} airborne legs: "
        + ", ".join(f"{k} {v}" for k, v in sorted(summary.items()))
    )
    print(f"type designators seen: {len(codes)}; unclassified legs: {sum(unknown.values())}")
    print("most common unclassified designators:", unknown.most_common(12))
    heights = Counter(f.height_source for f in flights)
    print("height sources:", dict(heights))

    # Spot check: random aircraft with at least 30 points, excluding non-ICAO addresses.
    rng = np.random.default_rng(args.seed)
    eligible = [
        str(i) for i, c in zip(uniq, counts, strict=True) if c >= 30 and not str(i).startswith("~")
    ]
    picks = list(rng.choice(eligible, size=min(args.sample, len(eligible)), replace=False))
    t_start = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
    report = []
    all_ok = True
    for icao in picks:
        url = history_trace_url(day, icao)
        path = root / "adsb" / "_spotcheck" / f"{day.isoformat()}_{icao}.json"
        download(url, path)
        remote = clip_trace(parse_trace(path.read_bytes()), bbox, t_start, t_start + 86400.0)
        mask = pc.equal(table.column("icao"), icao)
        local = table.filter(mask).sort_by("time")
        lt = (
            np.asarray(local.column("time").to_numpy()).astype("datetime64[ns]").astype("int64")
            / 1e9
        )
        ok = remote is not None and len(remote) == local.num_rows
        details = {
            "icao": icao,
            "cached_points": local.num_rows,
            "remote_points": 0 if remote is None else len(remote),
        }
        if ok:
            r_t = np.sort(remote.time_unix)
            r_lat = remote.lat[np.argsort(remote.time_unix)]
            r_lon = remote.lon[np.argsort(remote.time_unix)]
            dt = float(np.max(np.abs(r_t - lt)))
            dlat = float(np.max(np.abs(r_lat - local.column("lat").to_numpy())))
            dlon = float(np.max(np.abs(r_lon - local.column("lon").to_numpy())))
            geom_local = local.column("alt_geom_ft").to_numpy(zero_copy_only=False).astype(float)
            geom_remote = remote.alt_geom_ft[np.argsort(remote.time_unix)].astype(float)
            both = ~np.isnan(geom_local) & ~np.isnan(geom_remote)
            dalt = (
                float(np.max(np.abs(geom_local[both] - geom_remote[both]))) if both.any() else 0.0
            )
            ok = dt < 1e-3 and dlat < 1e-9 and dlon < 1e-9 and dalt < 1e-6
            details.update(
                {"max_dt_s": dt, "max_dlat": dlat, "max_dlon": dlon, "max_dalt_ft": dalt}
            )
        details["match"] = bool(ok)
        all_ok &= bool(ok)
        report.append(details)
        print(
            f"  {icao}: cached {details['cached_points']} points, "
            f"adsb.lol {details['remote_points']}; match={ok}"
        )
    matched = sum(1 for r in report if r["match"])
    print(f"spot check: {matched} of {len(report)} aircraft identical to adsb.lol")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "date": day.isoformat(),
                    "bbox": str(bbox),
                    "points": table.num_rows,
                    "aircraft": int(uniq.size),
                    "legs": len(flights),
                    "engine_classes": summary,
                    "unclassified_legs": sum(unknown.values()),
                    "unclassified_top": unknown.most_common(12),
                    "height_sources": dict(heights),
                    "spotcheck": report,
                },
                indent=2,
            )
        )
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
