"""M6 truth check: score a real case's true site by hand with the true flights.

The first thing to run when a real case fails. It lists the admitted flights that passed within
--within of the private truth during the clip (closest approach, heard time, slant, speed),
picks for each measured event the loudest flight whose heard closest approach falls within 45 s
of the event's, and scores the true site with those flights the way the pipeline would: per
event, with the event narrowed to the flight's speed. Then, for each event, a wide fundamental
sweep and a model-to-trace comparison over start offsets of +-40 s, which separate "the
measurement is wrong" (trace rms high, best offset far from zero) from "the search is wrong"
(the site scores well by hand but the run ranked it low). Every M6 bug so far was the former.

Usage, from the repo root:
  uv run python scripts/m6_truth_check.py --audio <file> --events <events.json>
      --truth <truth.json> --date 2026-11-04 --bbox W,S,E,N [--within 12000] [--km 40]
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from earwitness.aircraft import funnel as fn
from earwitness.aircraft.funnel import FlightSet, FunnelConfig, GroundHeight
from earwitness.aircraft.prep import prepare_flights
from earwitness.aircraft.score import (
    ScoreConfig,
    SpectrogramIndex,
    at_track_speed,
    score_candidate,
    score_event,
    trace_closeness,
)
from earwitness.audio.decode import read_audio
from earwitness.audio.events import EventSet, FlyoverEvent, Range
from earwitness.audio.spectrogram import spectrogram
from earwitness.core import geodesy
from earwitness.core.grid import BBox, parse_bbox
from earwitness.data.adsb_lol import cached_regions, load_day
from earwitness.data.aircraft_types import TypeTable
from earwitness.data.dem import Terrain
from earwitness.project import parse_utc

FloatArray = NDArray[np.float64]


def utc(ts: float) -> str:
    """HH:MM:SS of a Unix time."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%H:%M:%S")


def passing_flights(
    fs: FlightSet, xyz: FloatArray, start: float, duration: float, within_m: float
) -> list[tuple[float, float, float, int]]:
    """(closest-approach time, slant, speed, flight index) of flights that passed near the site."""
    rows: list[tuple[float, float, float, int]] = []
    for k, fl in enumerate(fs.flights):
        a, b = fl.span
        if b < start - 120.0 or a > start + duration + 120.0:
            continue
        tr = fs.track(k)
        m = (tr.t >= start - 120.0) & (tr.t <= start + duration + 120.0)
        if m.sum() < 3:
            continue
        t_cpa, slant = fn.cpa_to_points(tr.enu[m], tr.t[m], xyz[None, :])
        if slant[0] > within_m:
            continue
        v = float(np.interp(t_cpa[0], fl.time_unix, fl.gs_mps))
        rows.append((float(t_cpa[0]), float(slant[0]), v, k))
    rows.sort()
    return rows


def main() -> None:
    """Run the check."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--events", type=Path, required=True)
    ap.add_argument(
        "--truth", type=Path, required=True, help="private truth.json with lat, lon, start_utc"
    )
    ap.add_argument("--date", required=True, help="UTC date of the cached adsb.lol day")
    ap.add_argument("--bbox", nargs="+", required=True, help="the run's region, for the flight set")
    ap.add_argument(
        "--duration", type=float, default=None, help="clip length, s (default: the audio's)"
    )
    ap.add_argument("--within", type=float, default=12000.0, help="list flights within this, m")
    ap.add_argument("--km", type=float, default=40.0, help="load rows within this of the truth")
    args = ap.parse_args()

    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    tlat, tlon = float(truth["lat"]), float(truth["lon"])
    start = parse_utc(str(truth["start_utc"])).timestamp()
    region = parse_bbox(args.bbox)
    day = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=UTC)
    options = [
        (b, p) for b, p in cached_regions(None, day.date(), "prod") if b.contains(*region.center)
    ]
    if not options:
        raise SystemExit(f"no cached adsb.lol day covering the region for {args.date}")
    table = load_day(
        day.date(), options[-1][0], None, "prod", sub_bbox=BBox.around(tlat, tlon, args.km * 1000.0)
    )
    flights = prepare_flights(table, TypeTable.load(None))
    del table
    cfg = FunnelConfig()
    fs = FlightSet.build(
        flights,
        region,
        cfg,
        day.timestamp() - 4000.0,
        (day + timedelta(days=1)).timestamp() + 4000.0,
    )
    samples, rate = read_audio(args.audio, target_rate=4000)
    duration = args.duration or samples.size / rate
    spec = spectrogram(samples, rate, nfft=2048, hop=512, fmax=600.0)
    index = SpectrogramIndex(spec)
    scfg = ScoreConfig()
    ground = GroundHeight(Terrain(None))
    h = float(ground(np.array([tlat]), np.array([tlon]))[0])
    xyz = geodesy.geodetic_to_ecef(tlat, tlon, h)
    events = EventSet.load(args.events)
    print(
        f"truth start {utc(start)} UTC, clip {duration:.0f} s, ground {h:.0f} m; "
        f"{len(fs)} flights admitted of {len(flights)} within {args.km:.0f} km"
    )

    rows = passing_flights(fs, xyz, start, duration, args.within)
    print(f"\n{len(rows)} admitted flights within {args.within / 1000:.0f} km during the clip:")
    print(
        f"{'cpa utc':>8} {'clip s':>7} {'heard s':>8} {'slant m':>8} {'m/s':>5} {'class':>9} "
        f"{'type':>5} {'reg':>8} {'icao':>7}"
    )
    c = scfg.speed_of_sound
    for t_cpa, slant, v, k in rows:
        fl = fs.flights[k]
        print(
            f"{utc(t_cpa):>8} {t_cpa - start:7.1f} {t_cpa - start + slant / c:8.1f} {slant:8.0f} "
            f"{v:5.0f} {fl.engine_class:>9} {fl.type_code:>5} {fl.registration:>8} {fl.icao:>7}"
        )

    chosen: list[int] = []
    for ev in events.events:
        mid = ev.t0_s.mid if ev.valley is None else float(np.median(ev.valley[:, 3]))
        best: tuple[float, int, float, float, float] | None = None
        for t_cpa, slant, v, k in rows:
            heard = t_cpa - start + slant / c
            if abs(heard - mid) > 45.0:
                continue
            if best is None or slant < best[2]:
                best = (heard, k, slant, v, t_cpa)
        if best is None:
            raise SystemExit(
                f"no admitted flight passes near the site within 45 s of event {ev.id}"
            )
        heard, k, slant, v, t_cpa = best
        fl = fs.flights[k]
        band = ev.at_speed(v)
        print(
            f"\nevent {ev.id}: heard t0 range {ev.t0_s.lo:.1f}-{ev.t0_s.hi:.1f} s; loudest flight "
            f"{fl.registration or fl.icao} ({fl.type_code}) heard CPA {heard:.1f} s, "
            f"slant {slant:.0f} m, "
            f"{v:.0f} m/s (closest approach {t_cpa - start:.1f} s)"
        )
        print(
            f"   at that speed the event allows d "
            f"{band['d_m'][0][0]:.0f}-{band['d_m'][1][0]:.0f} m, "
            f"f0 {band['f0_hz'][0][0]:.1f}-{band['f0_hz'][1][0]:.1f} Hz, heard t0 "
            f"{band['t0_s'][0][0]:.1f}-{band['t0_s'][1][0]:.1f} s; "
            f"v range {ev.v_mps.lo:.0f}-{ev.v_mps.hi:.0f}"
        )
        chosen.append(k)
    tracks = [fs.track(k) for k in chosen]

    cs = score_candidate(index, tracks, xyz, events, start, scfg, None)
    parts = ", ".join(
        f"{e.score:.2f} dB at f0 {e.f0_hz:.1f} Hz dt {e.dt_s:+.1f}" for e in cs.events
    )
    print(
        f"\nhand score at the truth with those flights: total {cs.total:.2f}; "
        f"{parts}; start {utc(cs.t_start)}"
    )
    offs = np.arange(-40.0, 40.5, 1.0)
    for ev, tr in zip(events.events, tracks, strict=True):
        n = at_track_speed(ev, tr, xyz, start, 40.0)
        wide = FlyoverEvent(
            ev.id,
            n.t0_s,
            Range(max(10.0, n.f0_hz.mid - 60.0), n.f0_hz.mid + 60.0),
            ev.v_mps,
            n.d_m,
            harmonics=ev.harmonics,
            trace=ev.trace,
        )
        best_ev = score_event(index, tr, xyz, wide, start, scfg, 40.0)
        rms = np.array(
            [-trace_closeness(tr, xyz, wide, start + o, n.f0_hz.mid, scfg) for o in offs]
        )
        if not np.isfinite(rms).any():
            print(
                f"  {ev.id}: wide sweep best {best_ev.score:.2f} dB at f0 {best_ev.f0_hz:.1f} Hz; "
                f"trace not covered"
            )
            continue
        j = int(np.nanargmin(rms))
        print(
            f"  {ev.id}: wide sweep best {best_ev.score:.2f} dB at f0 {best_ev.f0_hz:.1f} Hz, dt "
            f"{best_ev.dt_s:+.1f} s; model-to-trace rms at dt 0 {rms[offs == 0.0][0]:.2f} Hz, best "
            f"{rms[j]:.2f} Hz at dt {offs[j]:+.0f} s"
        )


if __name__ == "__main__":
    main()
