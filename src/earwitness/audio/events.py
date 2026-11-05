"""Measured aircraft events: what the user (or a fitter) says about each flyover in a recording.

Per the handoff, measurements are stored as ranges, not points: the straight-line fit of a
Doppler curve can say the plane was near 100 m/s but surely not 70, and the funnel should use
exactly that. Times are seconds into the recording; `t0_s` is the heard closest-approach time,
the moment the received frequency crosses `f0`.

`events.json` layout (schema `earwitness.events/1`):

    {"schema": "earwitness.events/1",
     "recording": {"file": "clip.wav", "sha256": "..."},
     "known_time_utc": null,                    # or an ISO timestamp of the recording start
     "known_time_tolerance_s": 1.0,
     "events": [{"id": "e1", "t0_s": [280, 290], "f0_hz": [100, 120], "v_mps": [60, 80],
                 "d_m": [200, 800], "harmonics": 1, "trace": [[t_s, f_hz], ...] | null}],
     "silence": [{"start_s": 0, "end_s": 200}]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import numpy as np
from numpy.typing import ArrayLike, NDArray

from earwitness.project import parse_utc, utc_iso

SCHEMA = "earwitness.events/1"
FloatArray = NDArray[np.float64]


VALLEY_HALF_F0_HZ = 3.0
VALLEY_HALF_T0_S = 3.0
VALLEY_HALF_D_M = 100.0
VALLEY_HALF_D_FRACTION = 0.5
"""Half-widths around a valley row at a given speed: the floors the fit's ranges use."""


@dataclass(frozen=True)
class Range:
    """A closed interval of plausible values."""

    lo: float
    hi: float

    def __post_init__(self) -> None:
        if self.hi < self.lo:
            raise ValueError(f"range upside down: {self.lo} > {self.hi}")

    @property
    def mid(self) -> float:
        """Midpoint."""
        return 0.5 * (self.lo + self.hi)

    @property
    def half_width(self) -> float:
        """Half the width."""
        return 0.5 * (self.hi - self.lo)

    def contains(self, value: float, margin: float = 0.0) -> bool:
        """True when `value` is inside the range widened by `margin` on each side."""
        return self.lo - margin <= value <= self.hi + margin

    @classmethod
    def around(cls, center: float, half_width: float, lo_floor: float | None = None) -> Range:
        """Symmetric range, optionally clipped below."""
        lo = center - half_width
        if lo_floor is not None:
            lo = max(lo, lo_floor)
        return cls(lo, center + half_width)

    def as_list(self) -> list[float]:
        """[lo, hi] for JSON."""
        return [float(self.lo), float(self.hi)]


@dataclass(frozen=True)
class FlyoverEvent:
    """One measured propeller flyover."""

    id: str
    t0_s: Range
    """Heard closest-approach time, seconds into the recording."""
    f0_hz: Range
    """Rest fundamental frequency."""
    v_mps: Range
    """Speed near closest approach."""
    d_m: Range
    """Closest-approach slant distance."""
    harmonics: int = 1
    trace: FloatArray | None = None
    """Optional traced curve, shape (n, 2): [t_s, f_hz]."""
    valley: FloatArray | None = None
    """Optional speed profile, shape (n, 4): [v_mps, d_m, f0_hz, t0_s] rows the trace cannot
    tell apart (`earwitness.aircraft.fit.profile_speed`). The funnel and the scorer narrow the
    distance, fundamental and closest-approach time to a flight's speed with it."""

    def to_dict(self) -> dict[str, object]:
        """JSON form."""
        return {
            "id": self.id,
            "t0_s": self.t0_s.as_list(),
            "f0_hz": self.f0_hz.as_list(),
            "v_mps": self.v_mps.as_list(),
            "d_m": self.d_m.as_list(),
            "harmonics": self.harmonics,
            "trace": None if self.trace is None else self.trace.tolist(),
            "valley": None if self.valley is None else self.valley.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> FlyoverEvent:
        """Parse one event."""

        def rng(key: str) -> Range:
            lo, hi = d[key]  # type: ignore[misc]
            return Range(float(lo), float(hi))

        trace = d.get("trace")
        valley = d.get("valley")
        rows = None if not valley else np.asarray(valley, dtype=float)[:, :4]
        return cls(
            id=str(d["id"]),
            t0_s=rng("t0_s"),
            f0_hz=rng("f0_hz"),
            v_mps=rng("v_mps"),
            d_m=rng("d_m"),
            harmonics=int(d.get("harmonics", 1) or 1),
            trace=None if trace is None else np.asarray(trace, dtype=float).reshape(-1, 2),
            valley=rows,
        )

    def at_speed(self, v: ArrayLike) -> dict[str, tuple[FloatArray, FloatArray]]:
        """(lo, hi) arrays of d_m, f0_hz and t0_s for aircraft speeds `v`.

        With a valley, the trace's own distance, fundamental and closest-approach time at each
        speed, interpolated in log speed (distance log-log, since it grows as the speed squared
        along the valley) and held at the end rows beyond it, each widened by the floors the
        fit's ranges use and kept inside those ranges. Without one, the ranges themselves.
        """
        v = np.atleast_1d(np.asarray(v, dtype=float))

        def const(r: Range) -> tuple[FloatArray, FloatArray]:
            return np.full(v.shape, r.lo), np.full(v.shape, r.hi)

        if self.valley is None or self.valley.shape[0] == 0:
            return {"d_m": const(self.d_m), "f0_hz": const(self.f0_hz), "t0_s": const(self.t0_s)}
        rows = self.valley[np.argsort(self.valley[:, 0])]
        lv = np.log(np.clip(v, 1e-3, None))
        lrows = np.log(np.clip(rows[:, 0], 1e-3, None))
        d = np.exp(np.interp(lv, lrows, np.log(np.clip(rows[:, 1], 1e-3, None))))
        f0 = np.interp(lv, lrows, rows[:, 2])
        t0 = np.interp(lv, lrows, rows[:, 3])
        d_half = np.maximum(VALLEY_HALF_D_M, VALLEY_HALF_D_FRACTION * d)

        def band(
            mid: FloatArray, half: FloatArray | float, r: Range
        ) -> tuple[FloatArray, FloatArray]:
            lo = np.maximum(mid - half, r.lo)
            return lo, np.maximum(np.minimum(mid + half, r.hi), lo)

        return {
            "d_m": band(d, d_half, self.d_m),
            "f0_hz": band(f0, VALLEY_HALF_F0_HZ, self.f0_hz),
            "t0_s": band(t0, VALLEY_HALF_T0_S, self.t0_s),
        }

    def narrowed(self, v: float) -> FlyoverEvent:
        """This event with d_m, f0_hz and t0_s narrowed to speed `v` (see `at_speed`)."""
        if self.valley is None or self.valley.shape[0] == 0:
            return self
        r = self.at_speed(v)
        return replace(
            self,
            d_m=Range(float(r["d_m"][0][0]), float(r["d_m"][1][0])),
            f0_hz=Range(float(r["f0_hz"][0][0]), float(r["f0_hz"][1][0])),
            t0_s=Range(float(r["t0_s"][0][0]), float(r["t0_s"][1][0])),
        )


@dataclass(frozen=True)
class SilenceWindow:
    """A stretch of the recording in which no propeller aircraft is audible."""

    start_s: float
    end_s: float

    def __post_init__(self) -> None:
        if self.end_s <= self.start_s:
            raise ValueError("silence window must end after it starts")


@dataclass
class EventSet:
    """Everything measured about a recording's aircraft clues."""

    events: list[FlyoverEvent]
    silence: list[SilenceWindow] = field(default_factory=list)
    known_time: datetime | None = None
    """Recording start, when the user knows it."""
    known_time_tolerance_s: float = 1.0
    recording: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.events:
            raise ValueError("an event set needs at least one event")
        ids = [e.id for e in self.events]
        if len(set(ids)) != len(ids):
            raise ValueError("event ids must be unique")

    @property
    def single_event(self) -> bool:
        """True in known-time, one-flyover mode."""
        return len(self.events) == 1

    def gap(self) -> Range:
        """Heard closest-approach time difference between the first two events."""
        if len(self.events) < 2:
            raise ValueError("gap needs two events")
        a, b = self.events[0].t0_s, self.events[1].t0_s
        return Range(b.lo - a.hi, b.hi - a.lo)

    def to_dict(self) -> dict[str, object]:
        """JSON form."""
        return {
            "schema": SCHEMA,
            "recording": self.recording,
            "known_time_utc": None if self.known_time is None else utc_iso(self.known_time, True),
            "known_time_tolerance_s": self.known_time_tolerance_s,
            "events": [e.to_dict() for e in self.events],
            "silence": [{"start_s": s.start_s, "end_s": s.end_s} for s in self.silence],
        }

    def save(self, path: str | Path) -> Path:
        """Write events.json."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return p

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> EventSet:
        """Parse events.json content."""
        known = d.get("known_time_utc")
        return cls(
            events=[FlyoverEvent.from_dict(e) for e in d["events"]],  # type: ignore[union-attr]
            silence=[
                SilenceWindow(float(s["start_s"]), float(s["end_s"]))  # type: ignore[index]
                for s in d.get("silence", [])  # type: ignore[union-attr]
            ],
            known_time=None if known is None else parse_utc(str(known)),
            known_time_tolerance_s=float(d.get("known_time_tolerance_s", 1.0) or 1.0),
            recording=dict(d.get("recording", {}) or {}),  # type: ignore[arg-type]
        )

    @classmethod
    def load(cls, path: str | Path) -> EventSet:
        """Read events.json."""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def merge_spans(spans: list[tuple[float, float]], duration_s: float) -> list[tuple[float, float]]:
    """Clip spans to [0, duration], drop empty ones, and merge overlaps; sorted."""
    clipped = sorted((max(0.0, float(a)), min(duration_s, float(b))) for a, b in spans)
    merged: list[tuple[float, float]] = []
    for a, b in clipped:
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def audible_intervals(
    t_heard_cpa: FloatArray, half_widths: FloatArray, duration_s: float
) -> list[tuple[float, float]]:
    """Merge per-flyover audible intervals [t - w, t + w], clipped to the clip, sorted."""
    return merge_spans(
        [(float(t - w), float(t + w)) for t, w in zip(t_heard_cpa, half_widths, strict=True)],
        duration_s,
    )


def silence_between(
    audible: list[tuple[float, float]], duration_s: float, min_length_s: float = 30.0
) -> list[SilenceWindow]:
    """Silence windows: the gaps between audible intervals that are long enough to matter."""
    out: list[SilenceWindow] = []
    cursor = 0.0
    for a, b in audible:
        if a - cursor >= min_length_s:
            out.append(SilenceWindow(cursor, a))
        cursor = max(cursor, b)
    if duration_s - cursor >= min_length_s:
        out.append(SilenceWindow(cursor, duration_s))
    return out


def events_from_truth(
    truth: dict[str, object],
    max_events: int = 2,
    t0_half_width_s: float = 3.0,
    f0_half_width_hz: float = 3.0,
    v_fraction: float = 0.15,
    d_fraction: float = 0.5,
    audible_range_m: float = 4000.0,
    silence_min_s: float = 30.0,
    harmonics: int = 1,
    edge_margin_s: float = 0.0,
) -> EventSet:
    """Measurement ranges around a simulator's truth, the way a careful user would set them.

    Picks the `max_events` closest flyovers (smallest slant distance) whose heard closest
    approach lies at least `edge_margin_s` inside the clip, in time order, widens each truth
    value into a range, and marks as silence the gaps between the audible intervals. When the
    truth carries `audible_intervals_s` (recording-time spans in which any rendered source was
    within earshot, as the track-based harness writes them) those are used; otherwise each
    flyover is assumed straight and uniform and audible within `audible_range_m`.
    """
    all_flyovers = [e for e in truth["events"] if e.get("kind") == "flyover"]  # type: ignore[index]
    duration = float(truth["recording"]["duration_s"])  # type: ignore[index]
    speed_of_sound = float(truth.get("physics", {}).get("speed_of_sound_mps", 343.0))  # type: ignore[union-attr]
    heard = np.array([float(e["cpa_heard_time_s"]) for e in all_flyovers])
    in_clip = (heard > edge_margin_s) & (heard < duration - edge_margin_s)
    flyovers = [e for e, ok in zip(all_flyovers, in_clip, strict=True) if ok]
    if not flyovers:
        raise ValueError("truth has no flyover inside the clip")
    chosen = sorted(flyovers, key=lambda e: float(e["cpa_slant_m"]))[:max_events]
    chosen.sort(key=lambda e: float(e["cpa_heard_time_s"]))
    events = []
    for k, e in enumerate(chosen, start=1):
        v = float(e["speed_mps"])
        d = float(e["cpa_slant_m"])
        events.append(
            FlyoverEvent(
                id=f"e{k}",
                t0_s=Range.around(float(e["cpa_heard_time_s"]), t0_half_width_s),
                f0_hz=Range.around(float(e["f0_hz"]), f0_half_width_hz, lo_floor=1.0),
                v_mps=Range(v * (1.0 - v_fraction), v * (1.0 + v_fraction)),
                d_m=Range(max(d * (1.0 - d_fraction), 10.0), d * (1.0 + d_fraction)),
                harmonics=max(int(harmonics), 1),
            )
        )
    given = truth.get("audible_intervals_s")
    if given:
        audible = merge_spans([(float(a), float(b)) for a, b in given], duration)  # type: ignore[union-attr]
    else:
        heard_all = np.array([float(e["cpa_heard_time_s"]) for e in all_flyovers])
        slants = np.array([float(e["cpa_slant_m"]) for e in all_flyovers])
        speeds = np.array([float(e["speed_mps"]) for e in all_flyovers])
        half = np.sqrt(np.maximum(audible_range_m**2 - slants**2, 0.0)) / np.maximum(speeds, 1.0)
        half += audible_range_m / speed_of_sound
        audible = audible_intervals(heard_all, half, duration)
    silence = silence_between(audible, duration, silence_min_s)
    recording = dict(truth.get("recording", {}))  # type: ignore[arg-type]
    return EventSet(events=events, silence=silence, recording=recording)
