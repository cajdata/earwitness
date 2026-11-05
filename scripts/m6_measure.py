"""M6 measurement: trace and fit flyovers the way the UI does, and write events.json.

Each --event is "id,t_lo,t_hi,f_lo,f_hi[,harmonics]": the trace window in recording seconds
and the band of the line to follow in Hz (the fundamental, or any harmonic; the scorer only
needs a real Doppler line of that aircraft). The trace is the UI's ridge route: the brightest
bin per frame, an adaptive brightness margin, then following the line outward from its
brightest frame. The fit profiles the speed and the event carries the valley with heard
closest-approach times. A known recording time makes a single-event file; two events make a
two-event file, which a known time also constrains.

Usage, from the repo root:
  uv run python scripts/m6_measure.py <audio> <events.json> --event loud,690,775,70,120,3
      [--event second,770,860,150,320] [--known-time 2026-11-04T21:37:51Z --tolerance 2]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from earwitness.aircraft.fit import fit_straight_flyover
from earwitness.audio.decode import read_audio
from earwitness.audio.events import EventSet, FlyoverEvent, Range
from earwitness.audio.spectrogram import Spectrogram, ridge, spectrogram
from earwitness.project import parse_utc, sha256_file
from earwitness.server.app import follow_ridge

FloatArray = NDArray[np.float64]


def trace(
    spec: Spectrogram,
    f_lo: float,
    f_hi: float,
    t_lo: float,
    t_hi: float,
    min_db: float = 3.0,
    margin_db: float = 2.0,
    max_step: float = 0.04,
    max_gap_s: float = 1.5,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Traced (time, frequency, brightness) of the line in a band and window, the UI's way."""
    t, f = ridge(spec, f_lo, f_hi, whitened=True)
    rows = (spec.f >= f_lo) & (spec.f <= f_hi)
    bright = spec.whitened_db[rows].max(axis=0)
    keep = (bright >= min_db) & (t >= t_lo) & (t <= t_hi)
    dt = float(t[1] - t[0])
    width = max(1, round(2.0 / dt))
    smooth = np.convolve(bright, np.ones(width) / width, mode="same")
    window = keep | (bright < min_db)
    typical = float(np.median(smooth[window])) if window.any() else 0.0
    keep &= smooth >= typical + margin_db
    if keep.any():
        keep = follow_ridge(t, f, bright, keep, max_step, max_gap_s)
    return t[keep], f[keep], bright[keep]


def parse_event(raw: str) -> tuple[str, float, float, float, float, int]:
    """id, t_lo, t_hi, f_lo, f_hi and harmonics from one --event argument."""
    parts = raw.split(",")
    if len(parts) not in (5, 6):
        raise SystemExit(f"--event needs id,t_lo,t_hi,f_lo,f_hi[,harmonics]: {raw}")
    harmonics = int(parts[5]) if len(parts) == 6 else 1
    return parts[0], float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]), harmonics


def main() -> None:
    """Measure the events and write the file."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("audio", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument(
        "--event", action="append", required=True, help="id,t_lo,t_hi,f_lo,f_hi[,harmonics]"
    )
    ap.add_argument("--known-time", default=None, help="recording start, UTC ISO")
    ap.add_argument("--tolerance", type=float, default=None, help="known-time tolerance, s")
    ap.add_argument("--fmax", type=float, default=450.0, help="spectrogram ceiling, Hz")
    args = ap.parse_args()
    samples, rate = read_audio(args.audio, target_rate=4000)
    spec = spectrogram(samples, rate, nfft=4096, hop=512, fmax=args.fmax)
    events: list[FlyoverEvent] = []
    fits: dict[str, object] = {}
    for raw in args.event:
        eid, t_lo, t_hi, f_lo, f_hi, harmonics = parse_event(raw)
        t, f, b = trace(spec, f_lo, f_hi, t_lo, t_hi)
        if t.size < 5:
            print(
                f"{eid}: only {t.size} trace points in {t_lo}-{t_hi} s, {f_lo}-{f_hi} Hz; skipped"
            )
            continue
        fit = fit_straight_flyover(t, f)
        r = fit.ranges()
        rows = np.asarray(fit.heard_valley(), dtype=float)
        print(
            f"{eid}: {t.size} points, {t.min():.1f}-{t.max():.1f} s, "
            f"{f.min():.1f}-{f.max():.1f} Hz, "
            f"mean brightness {b.mean():.1f} dB"
        )
        print(
            f"  fit: f0 {fit.f0_hz:.2f} Hz, v {fit.v_mps:.1f} m/s, d {fit.d_m:.0f} m, "
            f"closest approach "
            f"{fit.t0_s:.2f} s, heard {fit.heard_t0_s():.2f} s, rms {fit.rms_hz:.2f} Hz; "
            f"valley {rows.shape[0]} rows, v {rows[:, 0].min():.0f}-{rows[:, 0].max():.0f} m/s"
        )
        print(f"  ranges: {json.dumps({k: [round(x, 1) for x in v] for k, v in r.items()})}")
        events.append(
            FlyoverEvent(
                id=eid,
                t0_s=Range(*r["t0_s"]),
                f0_hz=Range(*r["f0_hz"]),
                v_mps=Range(*r["v_mps"]),
                d_m=Range(*r["d_m"]),
                harmonics=harmonics,
                trace=np.column_stack([t, f]),
                valley=rows[:, :4],
            )
        )
        fits[eid] = fit.to_dict()
    if not events:
        raise SystemExit("no events measured")
    es = EventSet(
        events=events,
        known_time=parse_utc(args.known_time) if args.known_time else None,
        known_time_tolerance_s=args.tolerance if args.tolerance is not None else 1.0,
        recording={"file": args.audio.name, "sha256": sha256_file(args.audio)},
    )
    es.save(args.out)
    args.out.with_name(args.out.stem + "_fits.json").write_text(
        json.dumps(fits, indent=2), encoding="utf-8"
    )
    if len(events) >= 2:
        g = es.gap()
        print(
            f"gap between the first two events' heard closest approaches: "
            f"{g.mid:.1f} +- {g.half_width:.1f} s"
        )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
