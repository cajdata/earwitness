"""Exact-curve scoring on the whitened spectrogram.

For a candidate point and a flight, the received frequency is f0 times a geometry-only ratio
c / (c + v_r(t)), so the ratio is computed once per candidate and flight on a fine time grid and
every (time shift, f0) combination in the sweep is a cheap re-sampling. The score of a curve is
the mean whitened brightness (dB above the per-frequency median) along it, optionally summed
over harmonics. Event 1 sweeps a small time correction and f0; that fixes the recording start,
so event 2 sweeps f0 only. In single-event known-time mode the time sweep is the known-time
tolerance, and a traced curve, when given, is scored by closeness instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from earwitness.aircraft.model import Track
from earwitness.audio.events import EventSet, FlyoverEvent
from earwitness.audio.spectrogram import Spectrogram
from earwitness.core.physics import SPEED_OF_SOUND, doppler_curve

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class ScoreConfig:
    """Scoring parameters."""

    window_s: float = 60.0
    """Frames within this many seconds of the measured closest approach are scored."""
    time_sweep_s: float = 3.0
    time_step_s: float = 0.5
    f0_sweep_hz: float = 3.0
    f0_step_hz: float = 0.5
    harmonics: int = 1
    ratio_step_s: float = 0.1
    speed_of_sound: float = SPEED_OF_SOUND
    iterations: int = 5


class SpectrogramIndex:
    """Fast brightness lookups on a whitened spectrogram."""

    def __init__(self, spec: Spectrogram) -> None:
        self.t = spec.t
        self.f = spec.f
        self.db = spec.whitened_db
        self.dt = float(spec.t[1] - spec.t[0]) if spec.t.size > 1 else 1.0
        self.df = float(spec.f[1] - spec.f[0]) if spec.f.size > 1 else 1.0

    @property
    def duration(self) -> float:
        """Last frame time."""
        return float(self.t[-1])

    def frames_between(self, t_lo: float, t_hi: float) -> FloatArray:
        """Frame times inside [t_lo, t_hi]."""
        return self.t[(self.t >= max(t_lo, 0.0)) & (self.t <= min(t_hi, self.duration))]

    def brightness(self, t_s: FloatArray, f_hz: FloatArray) -> FloatArray:
        """Whitened dB at (time, frequency), linear in frequency, nearest frame in time.

        Works on arrays of any shape (time and frequency broadcast together). Frequencies off
        the spectrogram score as NaN.
        """
        t_s = np.asarray(t_s, dtype=float)
        f_hz = np.asarray(f_hz, dtype=float)
        col = np.clip(np.rint((t_s - self.t[0]) / self.dt).astype(int), 0, self.t.size - 1)
        pos = (f_hz - self.f[0]) / self.df
        finite = np.isfinite(pos)
        valid = finite & (pos >= 0.0) & (pos <= self.f.size - 1)
        pos_c = np.where(finite, np.clip(pos, 0.0, self.f.size - 1 - 1e-9), 0.0)
        lo = np.floor(pos_c).astype(int)
        frac = pos_c - lo
        hi = np.minimum(lo + 1, self.f.size - 1)
        col_b = np.broadcast_to(col, pos.shape)
        values = self.db[lo, col_b] * (1.0 - frac) + self.db[hi, col_b] * frac
        return np.where(valid, values, np.nan)


def frequency_ratio(
    track: Track, observer_xyz: FloatArray, t_abs: FloatArray, cfg: ScoreConfig
) -> FloatArray:
    """Received over rest frequency, c / (c + v_r), at absolute reception times.

    Times outside the track's span (allowing the acoustic delay) give NaN, so a leg that ends
    inside a scoring window simply contributes fewer frames.
    """
    t_abs = np.asarray(t_abs, dtype=float)
    t0, t1 = track.span
    inside = (t_abs >= t0) & (t_abs <= t1 + 60.0)
    out = np.full(t_abs.shape, np.nan)
    if inside.any():
        out[inside] = doppler_curve(
            track.position,
            track.velocity,
            observer_xyz,
            t_abs[inside],
            1.0,
            c=cfg.speed_of_sound,
            iterations=cfg.iterations,
        ).f
    return out


@dataclass(frozen=True)
class EventScore:
    """Best sweep result for one event."""

    score: float
    f0_hz: float
    dt_s: float
    t_start: float
    """Recording start implied by the best time shift, Unix seconds."""
    frames: int


def _sweep(
    index: SpectrogramIndex,
    track: Track,
    observer_xyz: FloatArray,
    event: FlyoverEvent,
    t_start_options: FloatArray,
    cfg: ScoreConfig,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Mean brightness for every (recording start option, f0 option). Returns (scores, dts, f0s)."""
    t_rec = index.frames_between(event.t0_s.mid - cfg.window_s, event.t0_s.mid + cfg.window_s)
    f0_half = max(cfg.f0_sweep_hz, event.f0_hz.half_width)
    f0s = np.arange(event.f0_hz.mid - f0_half, event.f0_hz.mid + f0_half + 1e-9, cfg.f0_step_hz)
    if t_rec.size == 0:
        return np.full((t_start_options.size, f0s.size), np.nan), t_start_options, f0s
    t_abs_lo = float(t_start_options.min() + t_rec[0] - cfg.ratio_step_s)
    t_abs_hi = float(t_start_options.max() + t_rec[-1] + cfg.ratio_step_s)
    grid = np.arange(t_abs_lo, t_abs_hi + cfg.ratio_step_s, cfg.ratio_step_s)
    ratio = frequency_ratio(track, observer_xyz, grid, cfg)
    scores = np.empty((t_start_options.size, f0s.size))
    weights = np.array([1.0 / n for n in range(1, cfg.harmonics + 1)])
    for i, t0 in enumerate(t_start_options):
        r = np.interp(t0 + t_rec, grid, ratio)
        total = np.zeros(f0s.size)
        for n, w in enumerate(weights, start=1):
            f = f0s[:, None] * (n * r[None, :])
            b = index.brightness(t_rec[None, :], f)
            total += w * np.nanmean(b, axis=1)
        scores[i] = total / weights.sum()
    return scores, t_start_options, f0s


def score_event(
    index: SpectrogramIndex,
    track: Track,
    observer_xyz: FloatArray,
    event: FlyoverEvent,
    t_start_base: float,
    cfg: ScoreConfig,
    time_sweep_s: float | None = None,
) -> EventScore:
    """Best (time shift, f0) for an event; `time_sweep_s` None means no time sweep."""
    if time_sweep_s is None or time_sweep_s <= 0.0:
        dts = np.zeros(1)
    else:
        dts = np.arange(-time_sweep_s, time_sweep_s + 1e-9, cfg.time_step_s)
    scores, starts, f0s = _sweep(index, track, observer_xyz, event, t_start_base + dts, cfg)
    if np.all(np.isnan(scores)):
        return EventScore(float("-inf"), event.f0_hz.mid, 0.0, t_start_base, 0)
    i, j = np.unravel_index(np.nanargmax(scores), scores.shape)
    n_frames = index.frames_between(
        event.t0_s.mid - cfg.window_s, event.t0_s.mid + cfg.window_s
    ).size
    return EventScore(float(scores[i, j]), float(f0s[j]), float(dts[i]), float(starts[i]), n_frames)


def trace_closeness(
    track: Track,
    observer_xyz: FloatArray,
    event: FlyoverEvent,
    t_start: float,
    f0: float,
    cfg: ScoreConfig,
) -> float:
    """Minus the RMS difference, Hz, between the modeled curve and the user's traced curve."""
    if event.trace is None or event.trace.shape[0] == 0:
        return float("nan")
    t_rec = event.trace[:, 0]
    ratio = frequency_ratio(track, observer_xyz, t_start + t_rec, cfg)
    return -float(np.sqrt(np.mean((f0 * ratio - event.trace[:, 1]) ** 2)))


@dataclass(frozen=True)
class CandidateScore:
    """Scores of one candidate point."""

    total: float
    events: tuple[EventScore, ...]
    t_start: float
    trace_rms_hz: float = float("nan")


def score_candidate(
    index: SpectrogramIndex,
    tracks: list[Track],
    observer_xyz: FloatArray,
    events: EventSet,
    t_start_base: float,
    cfg: ScoreConfig,
    time_sweep_s: float | None = None,
) -> CandidateScore:
    """Locked multi-event score: event 1 sweeps time and f0, later events sweep f0 only."""
    e1 = events.events[0]
    sweep = max(cfg.time_sweep_s, e1.t0_s.half_width) if time_sweep_s is None else time_sweep_s
    first = score_event(index, tracks[0], observer_xyz, e1, t_start_base, cfg, sweep)
    results = [first]
    t_start = first.t_start
    for event, track in zip(events.events[1:], tracks[1:], strict=True):
        results.append(score_event(index, track, observer_xyz, event, t_start, cfg, None))
    total = float(sum(r.score for r in results))
    trace_rms = float("nan")
    if events.single_event and e1.trace is not None:
        closeness = trace_closeness(tracks[0], observer_xyz, e1, t_start, first.f0_hz, cfg)
        trace_rms = -closeness
        total = closeness
    return CandidateScore(total, tuple(results), t_start, trace_rms)


def modeled_curve(
    track: Track,
    observer_xyz: FloatArray,
    event: FlyoverEvent,
    t_start: float,
    f0: float,
    cfg: ScoreConfig,
    index: SpectrogramIndex,
) -> tuple[FloatArray, FloatArray]:
    """(recording time, frequency) of the modeled curve across the event window, for overlays."""
    t_rec = index.frames_between(event.t0_s.mid - cfg.window_s, event.t0_s.mid + cfg.window_s)
    if t_rec.size == 0:
        return np.zeros(0), np.zeros(0)
    return t_rec, f0 * frequency_ratio(track, observer_xyz, t_start + t_rec, cfg)
