"""M6 scoring: a blind run folder against a private truth file.

Real recordings are analysed the way a user runs them, with the CLI or the UI and no truth in
sight. This script scores the run folder afterward against a truth file that lives under
data/ (gitignored) and prints the numbers docs/VALIDATION.md records: distance of the rank-1
answer from the true site, its score and margin over the runner-up, the rank of the first
listed candidate within the method's tolerance (200 m for aircraft, 5 km for thunder, the M4
and M2 acceptance widths), the start-time error when the truth records the clock, and, for
every result stage in results.parquet, the nearest scored candidate to the site and the rank of
the best candidate within the tolerance among that stage's candidates. Coordinates stay out of
the printed Markdown row, so it can be pasted into the repo as it is.

Truth file (JSON, private, never committed):

  {"lat": 40.0190, "lon": -86.2447, "start_utc": "2026-09-08T16:16:31.950Z",
   "notes": "phone on the porch, external mic"}

Only lat and lon are required. Example:

  uv run python scripts/m6_score.py --run runs/20261104T064154Z-recording-aircraft \\
      --truth data/m6/porch/truth.json --label porch --append data/m6/results.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from earwitness.core import geodesy
from earwitness.project import parse_utc, read_json, utc_iso, utc_now, write_json

DEFAULT_WITHIN_M = {"thunder": 5000.0, "aircraft": 200.0}
START_KEY = {"thunder": "offset_utc", "aircraft": "t_start_utc"}


def resolve_run(run: str, runs_dir: Path) -> Path:
    """A run folder given as a path or as a run id under `runs_dir`."""
    for candidate in (Path(run), runs_dir / run):
        if (candidate / "summary.json").exists():
            return candidate
    raise SystemExit(f"no summary.json in {run} or under {runs_dir}")


def method_of(summary: dict[str, object]) -> str:
    """'thunder' or 'aircraft' from the summary schema."""
    return "thunder" if "thunder" in str(summary.get("schema", "")) else "aircraft"


def load_truth(path: Path) -> tuple[float, float, datetime | None, dict[str, object]]:
    """Site, optional recording start and the raw truth mapping."""
    raw = read_json(path)
    if not isinstance(raw, dict) or "lat" not in raw or "lon" not in raw:
        raise SystemExit(f"{path} must be a JSON object with lat and lon")
    start = parse_utc(str(raw["start_utc"])) if raw.get("start_utc") else None
    return float(raw["lat"]), float(raw["lon"]), start, raw


def score_top(
    top: list[dict[str, object]],
    lat: float,
    lon: float,
    within_m: float,
    method: str,
    truth_start: datetime | None,
) -> dict[str, object]:
    """Rank-1 error, margin, first rank within tolerance and the start-time error."""
    if not top:
        return {"rank1_error_m": None, "rank1_score": None, "margin": None, "rank_within": None}
    dist = [
        float(geodesy.horizontal_distance(float(t["lat"]), float(t["lon"]), lat, lon))  # type: ignore[arg-type]
        for t in top
    ]
    best = top[0]
    scores = [float(t["score"]) for t in top]  # type: ignore[arg-type]
    rank_within = next(
        (int(t["rank"]) for t, d in zip(top, dist, strict=True) if d <= within_m), None
    )  # type: ignore[arg-type]
    start_error: float | None = None
    stamp = best.get(START_KEY[method])
    if truth_start is not None and stamp:
        start_error = (parse_utc(str(stamp)) - truth_start).total_seconds()
    flights = [
        f"{f['icao']}{' ' + str(f['type_code']) if f.get('type_code') else ''}"  # type: ignore[index]
        for f in best.get("flights", [])  # type: ignore[union-attr]
        if isinstance(f, dict) and f.get("icao")
    ]
    return {
        "rank1_error_m": dist[0],
        "rank1_score": scores[0],
        "margin": scores[0] - scores[1] if len(scores) > 1 else None,
        "rank_within": rank_within,
        "rank_within_error_m": dist[rank_within - 1] if rank_within else None,
        "start_error_s": start_error,
        "rank1_start": stamp,
        "rank1_flights": flights,
        "listed": len(top),
    }


def stage_ranks(results: Path, lat: float, lon: float, within_m: float) -> list[dict[str, object]]:
    """Per result stage: nearest candidate to the site, and the best candidate within tolerance."""
    if not results.exists():
        return []
    table = pq.read_table(results).to_pydict()
    n = len(table["lat"])
    stages = [str(s) for s in table["stage"]] if "stage" in table else ["coarse"] * n
    lat_arr = np.asarray(table["lat"], dtype=float)
    lon_arr = np.asarray(table["lon"], dtype=float)
    score = np.asarray(table["score"], dtype=float)
    dist = np.asarray(geodesy.horizontal_distance(lat_arr, lon_arr, lat, lon), dtype=float)
    out: list[dict[str, object]] = []
    for stage in dict.fromkeys(stages):
        mask = np.asarray([s == stage for s in stages]) & np.isfinite(dist) & np.isfinite(score)
        if not mask.any():
            continue
        idx = np.flatnonzero(mask)
        d, s = dist[idx], score[idx]
        nearest = int(np.argmin(d))
        row: dict[str, object] = {
            "stage": stage,
            "candidates": int(idx.size),
            "nearest_m": float(d[nearest]),
            "nearest_score": float(s[nearest]),
            "nearest_rank": int(np.sum(s > s[nearest])) + 1,
            "truth_rank": None,
            "truth_score": None,
        }
        near = d <= within_m
        if near.any():
            best = int(np.flatnonzero(near)[np.argmax(s[near])])
            row["truth_rank"] = int(np.sum(s > s[best])) + 1
            row["truth_score"] = float(s[best])
        out.append(row)
    return out


def fmt(value: object, spec: str, missing: str = "n/a") -> str:
    """Format a number, or a placeholder when it is missing."""
    return missing if value is None else format(value, spec)


def markdown_row(row: dict[str, object], within_m: float) -> str:
    """One table row without coordinates, for docs/VALIDATION.md."""
    top: dict[str, object] = row["top"]  # type: ignore[assignment]
    method = str(row["method"])
    mode = f" {row['mode']}" if row.get("mode") else ""
    score_spec = ".3f" if method == "thunder" else ".2f"
    margin = top.get("margin")
    score_txt = fmt(top.get("rank1_score"), score_spec)
    if margin is not None:
        score_txt += f", margin {fmt(margin, score_spec)}"
    tol = f"{within_m / 1000:g} km" if within_m >= 1000 else f"{within_m:g} m"
    rank_txt = f"{fmt(top.get('rank_within'), 'd', 'none')} ({tol})"
    start = top.get("start_error_s")
    start_txt = "n/a" if start is None else f"{start:+.1f}"
    stages: list[dict[str, object]] = row["stages"]  # type: ignore[assignment]
    final = stages[-1] if stages else None
    if final is None:
        truth_txt = "n/a"
    else:
        truth_txt = (
            f"{fmt(final['truth_rank'], 'd', 'none')} of {final['candidates']} "
            f"({final['stage']}; nearest {fmt(final['nearest_m'], '.0f')} m)"
        )
    counts = row.get("counts") or {}
    counts_txt = (
        ", ".join(f"{k} {v}" for k, v in counts.items()) if isinstance(counts, dict) else ""
    )  # type: ignore[union-attr]
    flights = top.get("rank1_flights") or []
    label = str(row["label"]) + (f" ({' + '.join(flights)})" if flights else "")  # type: ignore[arg-type]
    return (
        f"| {label} | {method}{mode} | {fmt(top.get('rank1_error_m'), '.0f')} | {rank_txt} | "
        f"{score_txt} | {start_txt} | {truth_txt} | {counts_txt} | "
        f"{fmt(row.get('runtime_s'), '.0f')} |"
    )


MARKDOWN_HEADER = (
    "| case | method | rank-1 error (m) | rank within tolerance | score, margin | "
    "start error (s) | truth rank in the final stage | counts | runtime (s) |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
)


def score_run(
    run_dir: Path, truth_path: Path, label: str, within_m: float | None
) -> tuple[dict[str, object], float]:
    """Score one run folder against one truth file."""
    summary = read_json(run_dir / "summary.json")
    if not isinstance(summary, dict):
        raise SystemExit(f"{run_dir / 'summary.json'} is not a JSON object")
    method = method_of(summary)
    tol = DEFAULT_WITHIN_M[method] if within_m is None else within_m
    lat, lon, start, raw = load_truth(truth_path)
    top = summary.get("top") or []
    recording = summary.get("recording") or {}
    row: dict[str, object] = {
        "label": label,
        "scored_utc": utc_iso(utc_now(), milliseconds=False),
        "run": str(run_dir),
        "recording": recording.get("file") if isinstance(recording, dict) else None,
        "method": method,
        "mode": summary.get("mode"),
        "within_m": tol,
        "truth": {
            "lat": lat,
            "lon": lon,
            "start_utc": raw.get("start_utc"),
            "notes": raw.get("notes"),
        },
        "priors": summary.get("priors"),
        "counts": summary.get("counts"),
        "runtime_s": summary.get("runtime_s"),
        "top": score_top(top, lat, lon, tol, method, start),  # type: ignore[arg-type]
        "stages": stage_ranks(run_dir / "results.parquet", lat, lon, tol),
    }
    return row, tol


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--run", required=True, help="run folder, or a run id under --runs-dir")
    ap.add_argument("--truth", type=Path, required=True, help="private truth JSON (lat, lon, ...)")
    ap.add_argument("--label", default=None, help="case name for the table (default: run id)")
    ap.add_argument("--runs-dir", type=Path, default=Path("runs"))
    ap.add_argument(
        "--within-m",
        type=float,
        default=None,
        help="tolerance (default 200 aircraft, 5000 thunder)",
    )
    ap.add_argument("--append", type=Path, default=None, help="JSON list to append the row to")
    ap.add_argument("--json", action="store_true", help="print the full row as JSON")
    args = ap.parse_args(argv)

    run_dir = resolve_run(args.run, args.runs_dir)
    row, tol = score_run(run_dir, args.truth, args.label or run_dir.name, args.within_m)
    top: dict[str, object] = row["top"]  # type: ignore[assignment]
    print(f"{row['method']}{' ' + str(row['mode']) if row.get('mode') else ''} run {run_dir.name}")
    print(
        f"  rank-1 error {fmt(top.get('rank1_error_m'), '.0f')} m, score "
        f"{fmt(top.get('rank1_score'), '.3f')}, margin {fmt(top.get('margin'), '.3f')}; "
        f"first rank within {tol:g} m: {fmt(top.get('rank_within'), 'd', 'none')}; "
        f"start error {fmt(top.get('start_error_s'), '+.2f')} s; runtime "
        f"{fmt(row.get('runtime_s'), '.0f')} s"
    )
    for st in row["stages"]:  # type: ignore[union-attr]
        print(
            f"  stage {st['stage']}: {st['candidates']} candidates, nearest "
            f"{fmt(st['nearest_m'], '.0f')} m at rank {st['nearest_rank']}, best within "
            f"{tol:g} m at rank {fmt(st['truth_rank'], 'd', 'none')}"
        )
    print()
    print(MARKDOWN_HEADER)
    print(markdown_row(row, tol))
    if args.json:
        print()
        print(json.dumps(row, indent=2))
    if args.append:
        existing = read_json(args.append) if args.append.exists() else []
        rows = list(existing) if isinstance(existing, list) else []
        rows.append(row)
        write_json(args.append, rows)
        print(f"\nappended to {args.append} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
