"""Command-line entry point: `earwitness <command>`. The planned surface is in docs/HANDOFF.md.

M1 implements `simulate thunder` and `simulate aircraft`. The other commands exist so the help
text shows the full shape of the tool, and exit with code 3 until their milestone lands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from earwitness import __version__

if TYPE_CHECKING:
    from earwitness.simulate import MixOptions
    from earwitness.simulate.thunder_synth import Strike

NOT_IMPLEMENTED = 3
DEFAULT_RATE = 16000


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with the subcommands from docs/HANDOFF.md."""
    parser = argparse.ArgumentParser(
        prog="earwitness",
        description="Geolocate a recording from background thunder or prop-aircraft sound.",
    )
    parser.add_argument("--version", action="version", version=f"earwitness {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    data = sub.add_parser("data", help="download public data into the local cache (M2, M3)")
    data_sub = data.add_subparsers(dest="source", metavar="<source>")
    for name, text in (("glm", "GOES GLM lightning (M2)"), ("adsb", "adsb.lol traces (M3)")):
        p = data_sub.add_parser(name, help=text)
        p.add_argument("--date", required=True, help="UTC date, YYYY-MM-DD")
        p.add_argument("--bbox", required=True, help="west,south,east,north in degrees")
        p.set_defaults(func=_not_implemented, milestone="M2" if name == "glm" else "M3")

    sim = sub.add_parser("simulate", help="write a synthetic recording plus truth.json")
    sim_sub = sim.add_subparsers(dest="method", metavar="<method>")
    _build_simulate_thunder(sim_sub.add_parser("thunder", help="synthetic thunder case"))
    _build_simulate_aircraft(sim_sub.add_parser("aircraft", help="synthetic prop-aircraft case"))

    ana = sub.add_parser("analyze", help="locate a recording (M2 thunder, M4 aircraft)")
    ana_sub = ana.add_subparsers(dest="method", metavar="<method>")
    for name, ms in (("thunder", "M2"), ("aircraft", "M4")):
        p = ana_sub.add_parser(name, help=f"{name} method ({ms})")
        p.add_argument("file", help="local audio or video file")
        p.set_defaults(func=_not_implemented, milestone=ms)

    audit = sub.add_parser("audit", help="report which location clues a recording contains")
    audit.add_argument("file", help="local audio or video file")
    audit.set_defaults(func=_not_implemented, milestone="M5")

    ui = sub.add_parser("ui", help="open the desktop UI (M5)")
    ui.set_defaults(func=_not_implemented, milestone="M5")
    return parser


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
    p.add_argument(
        "--strikes",
        type=Path,
        default=None,
        help="JSON file of strikes (time_utc or time_s, lat, lon, kind) used instead of a storm",
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


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"earwitness: not implemented yet (planned for {args.milestone})", file=sys.stderr)
    return NOT_IMPLEMENTED


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


def _load_strikes(path: Path, start_unix: float) -> list[Strike]:
    from earwitness.project import parse_utc
    from earwitness.simulate.thunder_synth import Strike

    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["strikes"] if isinstance(raw, dict) else raw
    strikes = []
    for row in rows:
        if "time_s" in row:
            t = float(row["time_s"])
        else:
            t = parse_utc(str(row["time_utc"])).timestamp() - start_unix
        strikes.append(Strike(t, float(row["lat"]), float(row["lon"]), str(row.get("kind", "CG"))))
    return strikes


def _simulate_thunder(args: argparse.Namespace) -> int:
    from earwitness.project import parse_utc
    from earwitness.simulate import write_thunder_case
    from earwitness.simulate.thunder_synth import ThunderScenario, random_storm

    start = parse_utc(args.start)
    duration = args.minutes * 60.0
    seed, rng = _seed_and_rng(args.seed)
    if args.strikes is not None:
        strikes = _load_strikes(args.strikes, start.timestamp())
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
