"""Command-line entry point: `earwitness <command>`. The planned surface is in docs/HANDOFF.md.

Implemented: `simulate thunder|aircraft` (M1), `data glm` and `analyze thunder` (M2), `data adsb`
(M3), `analyze aircraft` (M4), `audit` and `ui` (M5).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from earwitness import __version__

if TYPE_CHECKING:
    from earwitness.simulate import MixOptions
    from earwitness.simulate.thunder_synth import Strike

DEFAULT_RATE = 16000
BBOX_HELP = "west south east north in degrees (or --bbox=w,s,e,n)"


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with the subcommands from docs/HANDOFF.md."""
    parser = argparse.ArgumentParser(
        prog="earwitness",
        description="Geolocate a recording from background thunder or prop-aircraft sound.",
    )
    parser.add_argument("--version", action="version", version=f"earwitness {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    data = sub.add_parser("data", help="download public data into the local cache")
    data_sub = data.add_subparsers(dest="source", metavar="<source>")
    _build_data_glm(data_sub.add_parser("glm", help="GOES GLM lightning flashes (NOAA, AWS)"))
    _build_data_adsb(data_sub.add_parser("adsb", help="adsb.lol daily traces (ODbL)"))

    sim = sub.add_parser("simulate", help="write a synthetic recording plus truth.json")
    sim_sub = sim.add_subparsers(dest="method", metavar="<method>")
    _build_simulate_thunder(sim_sub.add_parser("thunder", help="synthetic thunder case"))
    _build_simulate_aircraft(sim_sub.add_parser("aircraft", help="synthetic prop-aircraft case"))

    ana = sub.add_parser("analyze", help="locate a recording")
    ana_sub = ana.add_subparsers(dest="method", metavar="<method>")
    _build_analyze_thunder(ana_sub.add_parser("thunder", help="thunder against lightning records"))
    _build_analyze_aircraft(
        ana_sub.add_parser("aircraft", help="prop-aircraft Doppler against ADS-B")
    )

    _build_audit(sub.add_parser("audit", help="report which location clues a recording contains"))

    _build_ui(sub.add_parser("ui", help="open the desktop UI"))
    return parser


# ----------------------------------------------------------------------------- data glm


def _build_data_glm(p: argparse.ArgumentParser) -> None:
    p.add_argument("--date", required=True, help="first UTC date, YYYY-MM-DD")
    p.add_argument("--date-end", default=None, help="last UTC date, inclusive (default: --date)")
    p.add_argument("--bbox", required=True, nargs="+", help=BBOX_HELP)
    p.add_argument(
        "--hours", default="0-23", help="UTC hour range per day, e.g. 18-23 (default 0-23)"
    )
    p.add_argument(
        "--satellite", default="auto", help="auto, east, west, G16, G17, G18 or G19 (default auto)"
    )
    p.add_argument("--cache", type=Path, default=None, help="cache root (default data/cache)")
    p.add_argument("--workers", type=int, default=8, help="parallel downloads (default 8)")
    p.add_argument(
        "--export",
        type=Path,
        default=None,
        help="also write the filtered events (.parquet or .json)",
    )
    p.add_argument(
        "--all-quality", action="store_true", help="keep flashes with a nonzero quality flag"
    )
    p.set_defaults(func=_data_glm)


def _parse_hours(text: str) -> tuple[int, int]:
    parts = text.split("-")
    h0, h1 = (int(parts[0]), int(parts[-1]))
    if not (0 <= h0 <= h1 <= 23):
        raise argparse.ArgumentTypeError("hours must be like 18-23")
    return h0, h1


def _data_glm(args: argparse.Namespace) -> int:
    from earwitness.core.grid import parse_bbox
    from earwitness.data.adapters import LightningEvents, save_events
    from earwitness.data.glm import load_flashes, resolve_satellite
    from earwitness.project import parse_utc_date

    bbox = parse_bbox(args.bbox)
    h0, h1 = _parse_hours(args.hours)
    first = parse_utc_date(args.date)
    last = parse_utc_date(args.date_end) if args.date_end else first
    if last < first:
        print("earwitness: --date-end is before --date", file=sys.stderr)
        return 2
    satellite = resolve_satellite(args.satellite, first.date(), bbox)
    parts = []
    day = first
    while day <= last:
        start = day + timedelta(hours=h0)
        end = day + timedelta(hours=h1 + 1)
        parts.append(
            load_flashes(
                satellite,
                start,
                end,
                bbox,
                args.cache,
                good_only=not args.all_quality,
                workers=args.workers,
                log=print,
            )
        )
        day += timedelta(days=1)
    energies = [p.energy for p in parts if p.energy is not None]
    events = LightningEvents(
        time_unix=np.concatenate([p.time_unix for p in parts]),
        lat=np.concatenate([p.lat for p in parts]),
        lon=np.concatenate([p.lon for p in parts]),
        energy=np.concatenate(energies) if energies else None,
        source=f"GLM {satellite}",
    )
    print(
        f"{len(events)} flashes from {satellite} inside {bbox} "
        f"({args.date}, hours {h0:02d}-{h1:02d})"
    )
    if args.export is not None:
        save_events(args.export, events)
        print(f"exported to {args.export}")
    return 0


# ----------------------------------------------------------------------------- data adsb


def _build_data_adsb(p: argparse.ArgumentParser) -> None:
    p.add_argument("--date", required=True, help="UTC date, YYYY-MM-DD")
    p.add_argument("--bbox", required=True, nargs="+", help=BBOX_HELP)
    p.add_argument(
        "--variant", choices=["prod", "staging"], default="prod", help="adsb.lol instance (prod)"
    )
    p.add_argument("--cache", type=Path, default=None, help="cache root (default data/cache)")
    p.add_argument(
        "--keep-raw", action="store_true", help="keep the downloaded tar parts after conversion"
    )
    p.add_argument(
        "--parts",
        type=Path,
        nargs="+",
        default=None,
        help="already downloaded tar parts to read instead of fetching the release",
    )
    p.add_argument("--force", action="store_true", help="rebuild even if the day is cached")
    p.set_defaults(func=_data_adsb)


def _data_adsb(args: argparse.Namespace) -> int:
    from earwitness.aircraft.prep import flights_summary, prepare_flights
    from earwitness.core.grid import parse_bbox
    from earwitness.data.adsb_lol import build_day, day_cache_path, load_day
    from earwitness.data.aircraft_types import TypeTable
    from earwitness.data.cache import cache_root
    from earwitness.project import parse_utc_date

    bbox = parse_bbox(args.bbox)
    day = parse_utc_date(args.date).date()
    root = cache_root(args.cache)
    dest = day_cache_path(root, day, bbox, args.variant)
    if dest.exists() and not args.force:
        print(f"cached: {dest}")
    else:
        stats = build_day(
            day, bbox, root, args.variant, parts=args.parts, keep_raw=args.keep_raw, log=print
        )
        print(
            f"{stats.traces_read} traces read; {stats.aircraft_kept} aircraft and "
            f"{stats.points_kept} points kept"
        )
    table = load_day(day, bbox, root, args.variant)
    try:
        types = TypeTable.load(root)
    except RuntimeError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        types = None
    flights = prepare_flights(table, types)
    counts = flights_summary(flights)
    print(
        f"{table.num_rows} points, {len(flights)} airborne legs: "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    )
    print(f"parquet: {dest}")
    return 0


# ----------------------------------------------------------------------------- analyze thunder


def _build_analyze_thunder(p: argparse.ArgumentParser) -> None:
    p.add_argument("file", help="local audio or video file")
    when = p.add_argument_group("time prior")
    when.add_argument("--date", required=True, help="UTC date the clip was recorded, YYYY-MM-DD")
    when.add_argument("--date-end", default=None, help="last possible UTC date, inclusive")
    when.add_argument(
        "--time-window",
        nargs=2,
        metavar=("START", "END"),
        default=None,
        help="narrower UTC window for the clip start, two ISO timestamps",
    )
    where = p.add_argument_group("region prior")
    where.add_argument("--bbox", default=None, nargs="+", help=BBOX_HELP)
    where.add_argument("--region", type=Path, default=None, help="GeoJSON polygon file")
    src = p.add_argument_group("lightning data")
    src.add_argument(
        "--strikes", type=Path, default=None, help="events file (.json or .parquet) instead of GLM"
    )
    src.add_argument("--satellite", default="auto", help="GLM satellite (default auto)")
    src.add_argument("--cache", type=Path, default=None, help="cache root (default data/cache)")
    tune = p.add_argument_group("search")
    tune.add_argument("--step", type=float, default=5.0, help="coarse offset step, s (default 5)")
    tune.add_argument("--rmax-km", type=float, default=25.0, help="audible radius (default 25)")
    tune.add_argument(
        "--min-strikes", type=int, default=None, help="prefilter N (default from the peak count)"
    )
    tune.add_argument("--grid-km", type=float, default=5.0, help="coarse grid spacing (default 5)")
    tune.add_argument(
        "--top", type=int, default=10, help="regions to refine and report (default 10)"
    )
    tune.add_argument("--no-refine", action="store_true", help="stop after the coarse pass")
    tune.add_argument("--no-land-mask", action="store_true", help="keep candidates at sea")
    tune.add_argument("--land-scale", default="50m", choices=["10m", "50m", "110m"])
    tune.add_argument(
        "--bearing-weight",
        type=float,
        default=0.0,
        help="bearing-diversity weighting strength, 0 to 1 (default 0, off)",
    )
    tune.add_argument("--mask-wind", action="store_true", help="zero wind-flagged frames")
    tune.add_argument(
        "--score",
        choices=["cosine", "pearson"],
        default="cosine",
        help="window score: cosine (per the handoff) or pearson (mean removed)",
    )
    out = p.add_argument_group("output")
    out.add_argument("--out", type=Path, default=None, help="run folder (default runs/<stamp>)")
    out.add_argument("--quiet", action="store_true", help="no progress output")
    p.set_defaults(func=_analyze_thunder)


def _analyze_thunder(args: argparse.Namespace) -> int:
    import json

    from earwitness.core.grid import geometry_bbox, geometry_from_geojson, parse_bbox
    from earwitness.data.adapters import load_events
    from earwitness.data.landmask import LandMask
    from earwitness.project import parse_utc, parse_utc_date, utc_now
    from earwitness.thunder.pipeline import PipelineConfig, ThunderPriors, analyze_thunder
    from earwitness.thunder.search import SearchConfig

    recording = Path(args.file)
    if not recording.exists():
        print(f"earwitness: no such file {recording}", file=sys.stderr)
        return 2
    if args.time_window:
        start, end = parse_utc(args.time_window[0]), parse_utc(args.time_window[1])
    else:
        start = parse_utc_date(args.date)
        end = (parse_utc_date(args.date_end) if args.date_end else start) + timedelta(days=1)
    region = geometry = None
    if args.region is not None:
        geometry = geometry_from_geojson(json.loads(args.region.read_text(encoding="utf-8")))
        region = geometry_bbox(geometry)
    elif args.bbox:
        region = parse_bbox(args.bbox)
    if args.strikes is not None:
        events = load_events(args.strikes)
    else:
        if region is None:
            print("earwitness: GLM needs --bbox or --region (or pass --strikes)", file=sys.stderr)
            return 2
        from earwitness.data.glm import load_flashes, resolve_satellite

        satellite = resolve_satellite(args.satellite, start.date(), region)
        margin = timedelta(hours=1)
        events = load_flashes(
            satellite,
            start - margin,
            end + margin,
            region.expand(args.rmax_km * 1000.0),
            args.cache,
            log=None if args.quiet else print,
        )
    if len(events) == 0:
        print("earwitness: no lightning events in the prior window", file=sys.stderr)
        return 1
    landmask = None if args.no_land_mask else LandMask.natural_earth(args.cache, args.land_scale)
    cfg = PipelineConfig(
        search=SearchConfig(
            step_s=args.step,
            r_max_m=args.rmax_km * 1000.0,
            min_strikes=args.min_strikes,
            bearing_weight=args.bearing_weight,
            score_mode=args.score,
        ),
        grid_spacing_m=args.grid_km * 1000.0,
        top_k=args.top,
        do_refine=not args.no_refine,
        use_land_mask=not args.no_land_mask,
        mask_wind=args.mask_wind,
    )
    out_dir = args.out or Path("runs") / f"{utc_now():%Y%m%dT%H%M%SZ}-{recording.stem}"
    priors = ThunderPriors(start=start, end=end, region=region, region_geometry=geometry)
    summary = analyze_thunder(
        recording, events, priors, out_dir, cfg, landmask, progress=None if args.quiet else print
    )
    print(f"run folder: {out_dir}")
    print(f"{'rank':>4}  {'score':>6}  {'lat':>9}  {'lon':>10}  {'clip start (UTC)':<24}  strikes")
    for t in summary["top"]:  # type: ignore[union-attr]
        print(
            f"{t['rank']:>4}  {t['score']:6.3f}  {t['lat']:9.4f}  {t['lon']:10.4f}  "
            f"{t['offset_utc']:<24}  {t['n_strikes']}"
        )
    return 0


# ----------------------------------------------------------------------------- analyze aircraft


def _build_analyze_aircraft(p: argparse.ArgumentParser) -> None:
    p.add_argument("file", help="local audio or video file")
    p.add_argument(
        "--events", type=Path, required=True, help="events.json with the measured flyovers"
    )
    when = p.add_argument_group("time prior")
    when.add_argument("--date", required=True, help="UTC date the clip was recorded, YYYY-MM-DD")
    when.add_argument("--date-end", default=None, help="last possible UTC date, inclusive")
    when.add_argument(
        "--known-time", default=None, help="recording start, UTC ISO (overrides events.json)"
    )
    when.add_argument("--known-time-tolerance", type=float, default=None, help="seconds")
    where = p.add_argument_group("region prior")
    where.add_argument("--bbox", default=None, nargs="+", help=BBOX_HELP)
    where.add_argument("--region", type=Path, default=None, help="GeoJSON polygon file")
    src = p.add_argument_group("aircraft data")
    src.add_argument(
        "--tracks",
        type=Path,
        default=None,
        help="simulator tracks.json instead of the adsb.lol cache",
    )
    src.add_argument("--variant", default="prod", help="cached adsb.lol instance (default prod)")
    src.add_argument("--cache", type=Path, default=None, help="cache root (default data/cache)")
    tune = p.add_argument_group("search")
    tune.add_argument("--silence", choices=["hard", "soft", "off"], default="hard")
    tune.add_argument("--no-terrain", action="store_true", help="flat ground at the ellipsoid")
    tune.add_argument("--top", type=int, default=10, help="flight pairs to refine and report")
    tune.add_argument("--max-candidates", type=int, default=30000)
    tune.add_argument("--harmonics", type=int, default=1, help="harmonics to score (default 1)")
    out = p.add_argument_group("output")
    out.add_argument("--out", type=Path, default=None, help="run folder (default runs/<stamp>)")
    out.add_argument("--quiet", action="store_true", help="no progress output")
    p.set_defaults(func=_analyze_aircraft)


def _analyze_aircraft(args: argparse.Namespace) -> int:
    import json

    from earwitness.aircraft.funnel import FunnelConfig
    from earwitness.aircraft.pipeline import (
        AircraftPipelineConfig,
        AircraftPriors,
        analyze_aircraft,
    )
    from earwitness.aircraft.prep import flights_from_tracks_json, prepare_flights
    from earwitness.aircraft.score import ScoreConfig
    from earwitness.audio.events import EventSet
    from earwitness.core.grid import geometry_bbox, geometry_from_geojson, parse_bbox
    from earwitness.project import parse_utc, parse_utc_date, utc_now

    recording = Path(args.file)
    if not recording.exists():
        print(f"earwitness: no such file {recording}", file=sys.stderr)
        return 2
    events = EventSet.load(args.events)
    if args.known_time:
        events.known_time = parse_utc(args.known_time)
    if args.known_time_tolerance is not None:
        events.known_time_tolerance_s = args.known_time_tolerance
    start = parse_utc_date(args.date)
    end = (parse_utc_date(args.date_end) if args.date_end else start) + timedelta(days=1)
    if args.region is not None:
        geometry = geometry_from_geojson(json.loads(args.region.read_text(encoding="utf-8")))
        region = geometry_bbox(geometry)
    elif args.bbox:
        region = parse_bbox(args.bbox)
    else:
        print("earwitness: aircraft analysis needs --bbox or --region", file=sys.stderr)
        return 2
    terrain = None
    if args.tracks is not None:
        flights = flights_from_tracks_json(args.tracks)
    else:
        from earwitness.data.adsb_lol import cached_regions, load_day
        from earwitness.data.aircraft_types import TypeTable

        day = start.date()
        options = [
            (b, p)
            for b, p in cached_regions(args.cache, day, args.variant)
            if b.contains(*region.center)
        ]
        if not options:
            print(
                f"earwitness: no cached adsb.lol day covering the region for {day}; "
                "run `earwitness data adsb` first",
                file=sys.stderr,
            )
            return 1
        cached_bbox, _ = options[-1]
        table = load_day(
            day, cached_bbox, args.cache, args.variant, sub_bbox=region.expand(12000.0)
        )
        flights = prepare_flights(table, TypeTable.load(args.cache))
    if not args.no_terrain:
        from earwitness.data.dem import Terrain

        terrain = Terrain(args.cache)
    cfg = AircraftPipelineConfig(
        funnel=FunnelConfig(silence_mode=args.silence),
        score=ScoreConfig(harmonics=args.harmonics),
        top_pairs=args.top,
        max_candidates=args.max_candidates,
        use_terrain=not args.no_terrain,
    )
    out_dir = args.out or Path("runs") / f"{utc_now():%Y%m%dT%H%M%SZ}-{recording.stem}-aircraft"
    priors = AircraftPriors(start=start, end=end, region=region)
    summary = analyze_aircraft(
        recording,
        events,
        flights,
        priors,
        out_dir,
        cfg,
        terrain,
        progress=None if args.quiet else print,
    )
    print(f"run folder: {out_dir}")
    print(f"{'rank':>4}  {'score':>7}  {'lat':>9}  {'lon':>10}  {'clip start (UTC)':<24}  flights")
    for t in summary["top"]:  # type: ignore[union-attr]
        names = " + ".join(f.get("icao", "?") for f in t["flights"] if f)
        print(
            f"{t['rank']:>4}  {t['score']:7.3f}  {t['lat']:9.4f}  {t['lon']:10.4f}  "
            f"{t['t_start_utc']:<24}  {names}"
        )
    return 0


# ----------------------------------------------------------------------------- simulate


def _add_simulate_common(
    p: argparse.ArgumentParser, default_minutes: float, default_snr_db: float
) -> None:
    where = p.add_argument_group("where and when")
    where.add_argument("--lat", type=float, required=True, help="observer latitude, degrees")
    where.add_argument("--lon", type=float, required=True, help="observer longitude, degrees")
    where.add_argument(
        "--elevation", type=float, default=0.0, help="observer ground elevation, m (default 0)"
    )
    where.add_argument(
        "--start", required=True, help="recording start, UTC ISO 8601 (2026-07-02T21:00:00Z)"
    )
    where.add_argument(
        "--minutes",
        type=float,
        default=default_minutes,
        help=f"clip length in minutes (default {default_minutes:g})",
    )
    out = p.add_argument_group("output")
    out.add_argument("--out", type=Path, required=True, help="case folder to write")
    out.add_argument("--seed", type=int, default=None, help="random seed (default: random)")
    out.add_argument(
        "--sample-rate",
        type=int,
        default=DEFAULT_RATE,
        help=f"WAV sample rate, Hz (default {DEFAULT_RATE})",
    )
    mix = p.add_argument_group("mixing")
    mix.add_argument(
        "--snr-db",
        type=float,
        default=default_snr_db,
        help=f"broadband SNR of the clean signal over the noise (default {default_snr_db:g})",
    )
    mix.add_argument("--noise", choices=["pink", "white", "none"], default="pink")
    mix.add_argument(
        "--codec",
        choices=["none", "aac", "mp3"],
        default="none",
        help="round-trip through a lossy codec (needs ffmpeg on PATH)",
    )
    mix.add_argument("--bitrate", type=int, default=96, help="codec bitrate, kbit/s (default 96)")
    mix.add_argument(
        "--highpass-hz", type=float, default=None, help="apply a device-like high-pass filter"
    )


def _build_simulate_thunder(p: argparse.ArgumentParser) -> None:
    _add_simulate_common(p, default_minutes=30.0, default_snr_db=10.0)
    storm = p.add_argument_group("storm (ignored when --strikes is given)")
    storm.add_argument("--n-strikes", type=int, default=20, help="number of strikes (default 20)")
    storm.add_argument(
        "--storm-bearing",
        type=float,
        default=None,
        help="bearing from the observer to the cell, deg (default random)",
    )
    storm.add_argument(
        "--storm-distance-km", type=float, default=12.0, help="distance to the cell (default 12)"
    )
    storm.add_argument(
        "--storm-radius-km", type=float, default=6.0, help="cell radius, km (default 6)"
    )
    storm.add_argument(
        "--storm-drift-mps", type=float, default=8.0, help="cell drift speed, m/s (default 8)"
    )
    storm.add_argument(
        "--storm-drift-bearing", type=float, default=None, help="drift direction (default random)"
    )
    storm.add_argument(
        "--ic-fraction", type=float, default=0.0, help="fraction of in-cloud flashes (default 0)"
    )
    real = p.add_argument_group("real strikes")
    real.add_argument(
        "--strikes",
        type=Path,
        default=None,
        help="events file (.json or .parquet, e.g. a GLM export) used instead of a random storm",
    )
    real.add_argument(
        "--strikes-radius-km",
        type=float,
        default=50.0,
        help="keep file strikes within this distance of the observer (default 50)",
    )
    p.set_defaults(func=_simulate_thunder)


def _build_simulate_aircraft(p: argparse.ArgumentParser) -> None:
    _add_simulate_common(p, default_minutes=20.0, default_snr_db=-20.0)
    fly = p.add_argument_group("flyovers")
    fly.add_argument("--flyovers", type=int, default=2, help="number of flyovers (default 2)")
    fly.add_argument("--speed-mps", type=float, default=70.0, help="speed, m/s (default 70)")
    fly.add_argument(
        "--height-m", type=float, default=450.0, help="height above the observer, m (default 450)"
    )
    fly.add_argument(
        "--lateral-m", type=float, default=300.0, help="closest-approach offset, m (default 300)"
    )
    fly.add_argument(
        "--gap-s",
        type=float,
        default=600.0,
        help="seconds between closest approaches (default 600)",
    )
    fly.add_argument(
        "--f0-hz", type=float, default=110.0, help="blade-passage frequency, Hz (default 110)"
    )
    fly.add_argument("--harmonics", type=int, default=8, help="harmonics per tone (default 8)")
    fly.add_argument(
        "--heading", type=float, default=None, help="first flyover heading, deg (default random)"
    )
    fly.add_argument(
        "--jitter",
        type=float,
        default=0.15,
        help="fractional random variation of speed, f0 and offset for later flyovers (0.15)",
    )
    p.set_defaults(func=_simulate_aircraft)


# ----------------------------------------------------------------------------- audit and ui


def _build_audit(p: argparse.ArgumentParser) -> None:
    p.add_argument("file", help="local audio or video file")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.set_defaults(func=_audit)


def _audit(args: argparse.Namespace) -> int:
    import json

    from earwitness.audit.report import audit_file

    recording = Path(args.file)
    if not recording.exists():
        print(f"earwitness: no such file {recording}", file=sys.stderr)
        return 2
    report = audit_file(recording)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.text())
    return 0


def _build_ui(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cache", type=Path, default=None, help="cache root (default data/cache)")
    p.add_argument("--runs-dir", type=Path, default=None, help="run folders (default runs/)")
    p.add_argument("--port", type=int, default=None, help="localhost port (default: a free one)")
    how = p.add_mutually_exclusive_group()
    how.add_argument(
        "--browser", action="store_true", help="open the system browser instead of a window"
    )
    how.add_argument(
        "--serve", action="store_true", help="only run the API server (front-end development)"
    )
    p.set_defaults(func=_ui)


def _ui(args: argparse.Namespace) -> int:
    from earwitness.server.launch import launch

    mode = "serve" if args.serve else "browser" if args.browser else "window"
    return launch(cache=args.cache, runs_dir=args.runs_dir, port=args.port, mode=mode)


def _seed_and_rng(seed: int | None) -> tuple[int, np.random.Generator]:
    if seed is None:
        seed = int.from_bytes(os.urandom(4), "big")
    return seed, np.random.default_rng(seed)


def _jitter(rng: np.random.Generator, fraction: float) -> float:
    return 1.0 + fraction * float(rng.uniform(-1.0, 1.0))


def _mix_options(args: argparse.Namespace) -> MixOptions:
    from earwitness.simulate import MixOptions

    return MixOptions(
        snr_db=None if args.noise == "none" else args.snr_db,
        noise=args.noise,
        codec=None if args.codec == "none" else args.codec,
        bitrate_kbps=args.bitrate,
        highpass_hz=args.highpass_hz,
    )


def _load_strikes(
    path: Path, start_unix: float, lat: float, lon: float, radius_m: float
) -> list[Strike]:
    from earwitness.core import geodesy
    from earwitness.data.adapters import load_events
    from earwitness.simulate.thunder_synth import Strike

    events = load_events(path, t_ref_unix=start_unix)
    near = geodesy.horizontal_distance(events.lat, events.lon, lat, lon) <= radius_m
    events = events.subset(near)
    kinds = ["CG"] * len(events) if events.kind is None else [str(k) for k in events.kind]
    return [
        Strike(float(t - start_unix), float(la), float(lo), k)
        for t, la, lo, k in zip(events.time_unix, events.lat, events.lon, kinds, strict=True)
    ]


def _simulate_thunder(args: argparse.Namespace) -> int:
    from earwitness.project import parse_utc
    from earwitness.simulate import write_thunder_case
    from earwitness.simulate.thunder_synth import ThunderScenario, random_storm

    start = parse_utc(args.start)
    duration = args.minutes * 60.0
    seed, rng = _seed_and_rng(args.seed)
    if args.strikes is not None:
        strikes = _load_strikes(
            args.strikes, start.timestamp(), args.lat, args.lon, args.strikes_radius_km * 1000.0
        )
        if not strikes:
            print("earwitness: no strikes within range of the observer", file=sys.stderr)
            return 1
    else:
        bearing = rng.uniform(0.0, 360.0) if args.storm_bearing is None else args.storm_bearing
        drift_bearing = (
            rng.uniform(0.0, 360.0)
            if args.storm_drift_bearing is None
            else args.storm_drift_bearing
        )
        strikes = random_storm(
            rng,
            args.lat,
            args.lon,
            duration,
            args.n_strikes,
            center_bearing_deg=bearing,
            center_distance_m=args.storm_distance_km * 1000.0,
            radius_m=args.storm_radius_km * 1000.0,
            drift_speed_mps=args.storm_drift_mps,
            drift_bearing_deg=drift_bearing,
            ic_fraction=args.ic_fraction,
        )
    scenario = ThunderScenario(args.lat, args.lon, args.elevation, start, duration, strikes)
    truth = write_thunder_case(args.out, scenario, args.sample_rate, _mix_options(args), seed, rng)
    events = list(truth["events"])
    heard = sum(1 for e in events if e["heard"])
    print(f"wrote {args.out}/: recording.wav, truth.json, strikes.json")
    print(f"observer {args.lat:.5f}, {args.lon:.5f}; start {truth['start_time_utc']}; seed {seed}")
    print(f"{len(events)} strikes, {heard} heard within the clip")
    return 0


def _simulate_aircraft(args: argparse.Namespace) -> int:
    from earwitness.project import parse_utc
    from earwitness.simulate import write_aircraft_case
    from earwitness.simulate.aircraft_synth import AircraftScenario, Flyover

    start = parse_utc(args.start)
    duration = args.minutes * 60.0
    seed, rng = _seed_and_rng(args.seed)
    n = args.flyovers
    if n < 1:
        print("earwitness: --flyovers must be at least 1", file=sys.stderr)
        return 2
    first_cpa = (duration - (n - 1) * args.gap_s) / 2.0
    if first_cpa < 30.0:
        need = ((n - 1) * args.gap_s + 60.0) / 60.0
        print(
            "earwitness: clip too short for that many flyovers at that gap "
            f"(need at least {need:.1f} minutes)",
            file=sys.stderr,
        )
        return 2
    heading0 = float(rng.uniform(0.0, 360.0)) if args.heading is None else args.heading
    flyovers = []
    for k in range(n):
        if k == 0:
            speed, height, lateral, f0, heading = (
                args.speed_mps,
                args.height_m,
                args.lateral_m,
                args.f0_hz,
                heading0,
            )
        else:
            speed = args.speed_mps * _jitter(rng, args.jitter)
            height = args.height_m * _jitter(rng, args.jitter)
            side = float(rng.choice([-1.0, 1.0]))
            lateral = args.lateral_m * _jitter(rng, args.jitter) * side
            f0 = args.f0_hz * _jitter(rng, args.jitter)
            heading = float(rng.uniform(0.0, 360.0))
        flyovers.append(
            Flyover(
                speed_mps=speed,
                height_m=height,
                heading_deg=heading,
                lateral_offset_m=lateral,
                t_cpa_s=first_cpa + k * args.gap_s,
                f0_hz=f0,
                harmonics=args.harmonics,
                icao=f"ad5b{k + 1:02d}",
            )
        )
    scenario = AircraftScenario(args.lat, args.lon, args.elevation, start, duration, flyovers)
    truth = write_aircraft_case(args.out, scenario, args.sample_rate, _mix_options(args), seed, rng)
    print(f"wrote {args.out}/: recording.wav, truth.json, tracks.json")
    print(f"observer {args.lat:.5f}, {args.lon:.5f}; start {truth['start_time_utc']}; seed {seed}")
    for e in list(truth["events"]):
        print(
            f"flyover {e['icao']}: f0 {e['f0_hz']:.1f} Hz, {e['speed_mps']:.1f} m/s, "
            f"CPA {e['cpa_slant_m']:.0f} m heard at t={e['cpa_heard_time_s']:.1f} s"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
