"""M4 acceptance: synthetic flyover recordings built from real adsb.lol flights, then located.

For each case the harness picks a site a few hundred meters beside a real propeller flight,
renders every admitted flight that comes within the silence distance of that site during a
recording window (exact Doppler curves on the real tracks, with harmonics and 1/r loudness,
mixed with pink noise), derives measurement ranges and silence windows from that truth the way a
careful user would, hides the truth, and runs the aircraft pipeline over the whole region and
the whole day. Two-event mode pairs the two closest flyovers; single-event mode uses the closest
one plus the known recording start.

Scores: distance of the rank-1 answer from the site, distance from the site's mirror image
across the event flight's path (a single straight flyover cannot tell the two apart), whether
the right flights were paired, candidate counts and runtime.

Example:
  uv run python scripts/m4_acceptance.py --date 2026-09-08 --bbox=-100,30,-85,42 \
      --cases 10 --out runs/m4
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from earwitness.aircraft.funnel import FlightSet, FunnelConfig, cpa_to_points
from earwitness.aircraft.pipeline import AircraftPipelineConfig, AircraftPriors, analyze_aircraft
from earwitness.aircraft.prep import Flight, prepare_flights
from earwitness.audio.decode import write_wav
from earwitness.audio.events import events_from_truth
from earwitness.core import geodesy
from earwitness.core.grid import BBox, parse_bbox
from earwitness.data.adsb_lol import cached_regions, load_day
from earwitness.data.aircraft_types import TypeTable
from earwitness.data.cache import cache_root
from earwitness.project import parse_utc_date, utc_iso, write_json
from earwitness.simulate import MixOptions, apply_mix
from earwitness.simulate.aircraft_synth import TrackSource, synthesize_from_tracks


def pick_case(
    fs: FlightSet,
    bbox: BBox,
    rng: np.random.Generator,
    clip_s: float,
    audible_m: float,
    event_max_m: float,
    min_gap_s: float,
    need_events: int,
) -> dict[str, object] | None:
    """A site beside a real flight with enough close flyovers in a window, or None."""
    speed_ok = (fs.sample_speed >= 40.0) & (fs.sample_speed <= 120.0)
    inner = bbox.expand(-15000.0)
    for _ in range(200):
        i = int(rng.choice(np.flatnonzero(speed_ok)))
        fl = fs.flights[int(fs.sample_flight[i])]
        t_s = float(fs.sample_t[i])
        lat_s = float(np.interp(t_s, fl.time_unix, fl.lat))
        lon_s = float(np.interp(t_s, fl.time_unix, fl.lon))
        h_s = float(np.interp(t_s, fl.time_unix, fl.height_m))
        if h_s > 2500.0 or not inner.contains(lat_s, lon_s):
            continue
        bearing = float(rng.uniform(0.0, 360.0))
        offset = float(rng.uniform(150.0, 700.0))
        site_lat, site_lon = geodesy.destination(lat_s, lon_s, bearing, offset)
        site = (float(site_lat), float(site_lon))
        t_start = t_s - float(rng.uniform(120.0, clip_s - 120.0))
        xy = fs.xy(np.array([site[0]]), np.array([site[1]]))[0]
        near = fs.tree().query_ball_point(xy, audible_m + 2000.0)
        if not near:
            continue
        near = np.asarray(near)
        in_window = (fs.sample_t[near] >= t_start - 60.0) & (
            fs.sample_t[near] <= t_start + clip_s + 60.0
        )
        flight_ids = np.unique(fs.sample_flight[near[in_window]])
        site_xyz = geodesy.geodetic_to_ecef(site[0], site[1], 0.0)
        flyovers = []
        for f in flight_ids:
            track = fs.track(int(f))
            t_cpa, slant = cpa_to_points(track.enu, track.t, site_xyz[None, :])
            t_cpa, slant = float(t_cpa[0]), float(slant[0])
            if slant > audible_m or not (t_start < t_cpa < t_start + clip_s):
                continue
            flyovers.append({"flight": int(f), "t_cpa": t_cpa, "slant": slant})
        close = sorted((x for x in flyovers if x["slant"] <= event_max_m), key=lambda x: x["t_cpa"])
        if len(close) < need_events:
            continue
        if need_events == 2 and all(
            abs(a["t_cpa"] - b["t_cpa"]) < min_gap_s for a in close for b in close if a is not b
        ):
            continue
        return {
            "site": site,
            "t_start": t_start,
            "flyovers": flyovers,
            "seed_flight": int(fs.sample_flight[i]),
        }
    return None


def render_case(
    fs: FlightSet,
    case: dict[str, object],
    clip_s: float,
    rate: int,
    snr_db: float,
    rng: np.random.Generator,
    out_dir: Path,
) -> dict[str, object]:
    """Write recording.wav and truth.json for a picked case."""
    site = case["site"]
    t_start = float(case["t_start"])
    site_xyz = geodesy.geodetic_to_ecef(site[0], site[1], 0.0)
    sources = []
    events = []
    for fo in case["flyovers"]:
        f = int(fo["flight"])
        fl: Flight = fs.flights[f]
        f0 = float(rng.uniform(80.0, 130.0))
        track = fs.track(f)
        sources.append(TrackSource(track, f0, harmonics=6, label=fl.icao))
        speed = float(np.interp(fo["t_cpa"], fl.time_unix, fl.gs_mps))
        events.append(
            {
                "kind": "flyover",
                "icao": fl.icao,
                "leg": fl.leg,
                "type": fl.type_code,
                "engine_class": fl.engine_class,
                "f0_hz": f0,
                "speed_mps": speed,
                "cpa_slant_m": float(fo["slant"]),
                "cpa_time_s": float(fo["t_cpa"]) - t_start,
                "cpa_heard_time_s": float(fo["t_cpa"]) - t_start + float(fo["slant"]) / 343.0,
            }
        )
    clean = synthesize_from_tracks(site_xyz, t_start, clip_s, rate, sources)
    mixed = apply_mix(clean, rate, MixOptions(snr_db=snr_db), rng)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_wav(out_dir / "recording.wav", mixed, rate)
    truth = {
        "schema": "earwitness.truth/1",
        "method": "aircraft",
        "observer": {"lat": site[0], "lon": site[1], "elevation_m": 0.0},
        "start_time_utc": utc_iso(datetime.fromtimestamp(t_start, tz=UTC), True),
        "start_time_unix": t_start,
        "recording": {"file": "recording.wav", "sample_rate_hz": rate, "duration_s": clip_s},
        "physics": {"speed_of_sound_mps": 343.0},
        "mix": {"snr_db": snr_db, "noise": "pink"},
        "events": sorted(events, key=lambda e: e["cpa_heard_time_s"]),
    }
    write_json(out_dir / "truth.json", truth)
    return truth


def mirror_point(fs: FlightSet, flight: int, site: tuple[float, float]) -> tuple[float, float]:
    """Reflection of the site across the flight's ground path near closest approach."""
    track = fs.track(flight)
    site_xyz = geodesy.geodetic_to_ecef(site[0], site[1], 0.0)
    t_cpa, _ = cpa_to_points(track.enu, track.t, site_xyz[None, :])
    tt = np.array([float(t_cpa[0]) - 5.0, float(t_cpa[0]) + 5.0])
    tt = np.clip(tt, track.span[0], track.span[1])
    pts = geodesy.ecef_to_geodetic(track.position(tt))
    enu = geodesy.geodetic_to_enu(pts[0], pts[1], 0.0, site[0], site[1], 0.0)
    a, b = enu[0, :2], enu[1, :2]
    direction = b - a
    norm = np.linalg.norm(direction)
    if norm < 1e-6:
        return site
    direction /= norm
    foot = a + direction * np.dot(-a, direction)
    mirror = 2.0 * foot
    lat, lon, _ = geodesy.enu_to_geodetic(
        np.array([mirror[0], mirror[1], 0.0]), site[0], site[1], 0.0
    )
    return float(lat), float(lon)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--date", required=True)
    ap.add_argument("--bbox", required=True, nargs="+", help="search region west south east north")
    ap.add_argument("--cases", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--snr-db", type=float, default=-15.0)
    ap.add_argument("--sample-rate", type=int, default=8000)
    ap.add_argument("--mode", choices=["two", "single"], default="two")
    ap.add_argument(
        "--event-max-m", type=float, default=1500.0, help="event flyovers closer than this"
    )
    ap.add_argument("--min-gap-s", type=float, default=90.0)
    ap.add_argument("--variant", default="prod")
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--max-candidates", type=int, default=30000)
    args = ap.parse_args(argv)

    bbox = parse_bbox(args.bbox)
    day = parse_utc_date(args.date)
    root = cache_root(args.cache)
    options = [
        (b, p)
        for b, p in cached_regions(root, day.date(), args.variant)
        if b.contains(*bbox.center)
    ]
    if not options:
        raise SystemExit(
            "no cached adsb.lol day covering the region; run `earwitness data adsb` first"
        )
    table = load_day(day.date(), options[-1][0], root, args.variant, sub_bbox=bbox.expand(12000.0))
    flights = prepare_flights(table, TypeTable.load(root))
    cfg = AircraftPipelineConfig(
        funnel=FunnelConfig(),
        top_pairs=args.top,
        max_candidates=args.max_candidates,
        use_terrain=False,
    )
    fs = FlightSet.build(flights, bbox, cfg.funnel)
    print(
        f"{len(flights)} legs in the region, {len(fs)} admitted (classes {cfg.funnel.keep_classes})"
    )
    rng = np.random.default_rng(args.seed)
    clip_s = args.minutes * 60.0
    need = 2 if args.mode == "two" else 1
    priors = AircraftPriors(day, day + timedelta(days=1), bbox)
    rows = []
    k = 0
    while k < args.cases:
        case = pick_case(
            fs,
            bbox,
            rng,
            clip_s,
            cfg.funnel.silence_distance_m,
            args.event_max_m,
            args.min_gap_s,
            need,
        )
        if case is None:
            print("no more usable sites")
            break
        k += 1
        case_dir = args.out / f"case_{k:02d}"
        truth = render_case(
            fs,
            case,
            clip_s,
            args.sample_rate,
            args.snr_db,
            np.random.default_rng(args.seed + k),
            case_dir / "truth",
        )
        events = events_from_truth(truth, max_events=need)
        if args.mode == "single":
            events.known_time = datetime.fromtimestamp(float(case["t_start"]), tz=UTC)
            events.known_time_tolerance_s = 1.0
        events.save(case_dir / "truth" / "events.json")
        site = case["site"]
        chosen = sorted(truth["events"], key=lambda e: e["cpa_slant_m"])[:need]
        chosen_txt = "; ".join(
            f"{e['icao']} {e['type']} {e['cpa_slant_m']:.0f} m at {e['cpa_heard_time_s']:.0f} s"
            for e in chosen
        )
        print(
            f"\ncase {k}: site {site[0]:.4f},{site[1]:.4f}; "
            f"{len(truth['events'])} audible flyovers; {chosen_txt}"
        )
        t0 = time.perf_counter()
        summary = analyze_aircraft(
            case_dir / "truth" / "recording.wav",
            events,
            flights,
            priors,
            case_dir / "run",
            cfg,
            None,
            progress=None,
        )
        runtime = time.perf_counter() - t0
        top = summary["top"]
        truth_icaos = [e["icao"] for e in sorted(chosen, key=lambda e: e["cpa_heard_time_s"])]
        row = {
            "case": k,
            "site": site,
            "flyovers_rendered": len(truth["events"]),
            "truth_icaos": truth_icaos,
            "candidates": summary["counts"]["candidates"],
            "after_silence": summary["counts"].get("after_silence"),
            "runtime_s": runtime,
            "top1_error_m": None,
            "top1_mirror_m": None,
            "top1_pair_correct": None,
            "rank_within_200m": None,
        }
        if top:
            best = top[0]
            d = float(geodesy.horizontal_distance(best["lat"], best["lon"], site[0], site[1]))
            event_flight = next(
                x["flight"]
                for x in case["flyovers"]
                if fs.flights[int(x["flight"])].icao == truth_icaos[0]
            )
            mlat, mlon = mirror_point(fs, int(event_flight), site)
            dm = float(geodesy.horizontal_distance(best["lat"], best["lon"], mlat, mlon))
            got = [f.get("icao") for f in best["flights"] if f]
            row.update(
                {
                    "top1_error_m": d,
                    "top1_mirror_m": dm,
                    "top1_pair_correct": got == truth_icaos,
                    "top1_score": best["score"],
                    "runner_up_score": top[1]["score"] if len(top) > 1 else None,
                    "rank_within_200m": next(
                        (
                            t["rank"]
                            for t in top
                            if geodesy.horizontal_distance(t["lat"], t["lon"], site[0], site[1])
                            <= 200.0
                        ),
                        None,
                    ),
                }
            )
        rows.append(row)
        err_txt = "n/a" if row["top1_error_m"] is None else f"{row['top1_error_m']:.0f}"
        mir_txt = "n/a" if row["top1_mirror_m"] is None else f"{row['top1_mirror_m']:.0f}"
        print(
            f"  rank-1 error {err_txt} m (mirror {mir_txt} m), "
            f"pair correct: {row['top1_pair_correct']}, "
            f"rank within 200 m: {row['rank_within_200m']}, "
            f"{row['candidates']} candidates, {runtime:.0f} s"
        )
    passed = sum(1 for r in rows if r["rank_within_200m"] == 1)
    passed_mirror = sum(
        1
        for r in rows
        if r["top1_error_m"] is not None and min(r["top1_error_m"], r["top1_mirror_m"]) <= 200.0
    )
    report = {
        "date": args.date,
        "bbox": str(bbox),
        "mode": args.mode,
        "cases": rows,
        "rank1_within_200m": passed,
        "rank1_within_200m_or_mirror": passed_mirror,
        "total": len(rows),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "m4_acceptance.json").write_text(json.dumps(report, indent=2))
    lines = [
        f"# M4 acceptance ({args.mode}-event): {args.date}, region {bbox}",
        "",
        "| case | flyovers rendered | event flights | rank-1 error (m) | mirror (m) | "
        "pair correct | rank within 200 m | candidates | runtime (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        e = "n/a" if r["top1_error_m"] is None else f"{r['top1_error_m']:.0f}"
        m = "n/a" if r["top1_mirror_m"] is None else f"{r['top1_mirror_m']:.0f}"
        flights_txt = " + ".join(r["truth_icaos"])
        lines.append(
            f"| {r['case']} | {r['flyovers_rendered']} | {flights_txt} | {e} | {m} | "
            f"{r['top1_pair_correct']} | {r['rank_within_200m']} | {r['candidates']} | "
            f"{r['runtime_s']:.0f} |"
        )
    lines += [
        "",
        f"Rank 1 within 200 m: {passed} of {len(rows)}; within 200 m of the site or its "
        f"mirror: {passed_mirror} of {len(rows)}.",
    ]
    (args.out / "m4_acceptance.md").write_text("\n".join(lines) + "\n")
    print(
        f"\nrank 1 within 200 m: {passed} of {len(rows)} (or mirror: {passed_mirror}); "
        f"report in {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
