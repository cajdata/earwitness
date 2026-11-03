"""M3 acceptance: summarize a cached adsb.lol day and spot-check flights against a second source.

Two references are available:

- `--reference site`: adsb.lol's per-day trace files (what its replay view loads), compared
  point for point. The site keeps those files only for some days, so missing files count as
  unavailable rather than as mismatches.
- `--reference staging` (default): adsb.lol's other instance, `readsb-staging-0`, whose daily
  release comes from a separate receiver set. Its traces are extracted from that release and
  compared by interpolating the cached (prod) flight at the staging timestamps: independent
  receivers decode the same ADS-B positions, so agreement should be tens of meters.

The script also reports the day's size, the engine-class breakdown of the prepared flights,
and the designators the type table could not classify.

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
from earwitness.core import geodesy
from earwitness.core.grid import parse_bbox
from earwitness.data.adsb_lol import (
    Trace,
    clip_trace,
    download_parts,
    extract_traces,
    history_trace_url,
    load_day,
    parse_trace,
)
from earwitness.data.aircraft_types import TypeTable
from earwitness.data.cache import cache_root, download
from earwitness.project import parse_utc_date


def compare_point_for_point(local: dict[str, np.ndarray], remote: Trace) -> dict[str, object]:
    """Exact comparison, for traces that come from the same source files."""
    order = np.argsort(remote.time_unix)
    out: dict[str, object] = {"remote_points": len(remote)}
    if len(remote) != local["t"].size:
        out["match"] = False
        return out
    dt = float(np.max(np.abs(remote.time_unix[order] - local["t"])))
    dlat = float(np.max(np.abs(remote.lat[order] - local["lat"])))
    dlon = float(np.max(np.abs(remote.lon[order] - local["lon"])))
    out.update({"max_dt_s": dt, "max_dlat_deg": dlat, "max_dlon_deg": dlon})
    out["match"] = dt < 1e-3 and dlat < 1e-9 and dlon < 1e-9
    return out


def compare_interpolated(
    local: dict[str, np.ndarray], remote: Trace, min_overlap: int = 10
) -> dict[str, object]:
    """Distance between the other instance's points and the cached flight interpolated in time."""
    t0, t1 = float(local["t"][0]), float(local["t"][-1])
    inside = (remote.time_unix >= t0) & (remote.time_unix <= t1) & ~remote.on_ground
    out: dict[str, object] = {
        "remote_points": len(remote),
        "overlap_points": int(inside.sum()),
    }
    if inside.sum() < min_overlap:
        out["match"] = None
        return out
    lat0, lon0 = float(local["lat"][0]), float(local["lon"][0])
    enu_local = geodesy.geodetic_to_enu(local["lat"], local["lon"], 0.0, lat0, lon0, 0.0)
    rt = remote.time_unix[inside]
    e = np.interp(rt, local["t"], enu_local[:, 0])
    n = np.interp(rt, local["t"], enu_local[:, 1])
    enu_remote = geodesy.geodetic_to_enu(
        remote.lat[inside], remote.lon[inside], 0.0, lat0, lon0, 0.0
    )
    d = np.hypot(enu_remote[:, 0] - e, enu_remote[:, 1] - n)
    gaps = np.array([np.min(np.abs(local["t"] - x)) for x in rt])
    out.update(
        {
            "median_m": float(np.median(d)),
            "p95_m": float(np.percentile(d, 95)),
            "max_m": float(np.max(d)),
            "median_nearest_gap_s": float(np.median(gaps)),
        }
    )
    out["match"] = bool(np.median(d) < 50.0 and np.percentile(d, 95) < 300.0)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--date", required=True)
    ap.add_argument("--bbox", required=True, nargs="+", help="west south east north")
    ap.add_argument("--variant", default="prod", help="cached instance to check")
    ap.add_argument("--reference", choices=["staging", "site"], default="staging")
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--sample", type=int, default=8, help="aircraft to compare")
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
    unknown = Counter(f.type_code for f in flights if f.engine_class == "unknown")
    heights = Counter(f.height_source for f in flights)
    print(
        f"{len(flights)} airborne legs: "
        + ", ".join(f"{k} {v}" for k, v in sorted(summary.items()))
    )
    print(f"unclassified legs: {sum(unknown.values())}; top designators: {unknown.most_common(8)}")
    print("height sources:", dict(heights))

    rng = np.random.default_rng(args.seed)
    eligible = [
        str(i) for i, c in zip(uniq, counts, strict=True) if c >= 30 and not str(i).startswith("~")
    ]
    picks = [
        str(p) for p in rng.choice(eligible, size=min(args.sample, len(eligible)), replace=False)
    ]
    t_start = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()

    remotes: dict[str, Trace] = {}
    if args.reference == "staging":
        parts = download_parts(day, root, "staging", log=print)
        print(f"extracting {len(picks)} aircraft from the staging release ...")
        remotes = extract_traces(parts, set(picks))
    else:
        for icao in picks:
            path = root / "adsb" / "_spotcheck" / f"{day.isoformat()}_{icao}.json"
            try:
                download(history_trace_url(day, icao), path)
            except RuntimeError:
                continue
            trace = parse_trace(path.read_bytes())
            if trace is not None:
                remotes[icao] = trace

    report = []
    for icao in picks:
        local_tbl = table.filter(pc.equal(table.column("icao"), icao)).sort_by("time")
        lt = np.asarray(local_tbl.column("time").to_numpy()).astype("datetime64[ns]")
        local = {
            "t": lt.astype("int64") / 1e9,
            "lat": local_tbl.column("lat").to_numpy(),
            "lon": local_tbl.column("lon").to_numpy(),
        }
        entry: dict[str, object] = {"icao": icao, "cached_points": local_tbl.num_rows}
        remote = remotes.get(icao)
        if remote is None:
            entry["match"] = None
            entry["note"] = "not in reference"
        else:
            clipped = clip_trace(remote, bbox, t_start, t_start + 86400.0)
            if clipped is None:
                entry["match"] = None
                entry["note"] = "reference has no points in the region"
            elif args.reference == "site":
                entry.update(compare_point_for_point(local, clipped))
            else:
                entry.update(compare_interpolated(local, clipped))
                entry["type_code_agrees"] = clipped.type_code == str(
                    local_tbl.column("type_code")[0]
                )
        report.append(entry)
        print(f"  {icao}: {json.dumps(entry)}")
    decided = [r for r in report if r["match"] is not None]
    matched = sum(1 for r in decided if r["match"])
    print(
        f"spot check ({args.reference}): {matched} of {len(decided)} compared aircraft agree; "
        f"{len(report) - len(decided)} unavailable"
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "date": day.isoformat(),
                    "bbox": str(bbox),
                    "reference": args.reference,
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
    return 0 if decided and matched == len(decided) else 1


if __name__ == "__main__":
    raise SystemExit(main())
