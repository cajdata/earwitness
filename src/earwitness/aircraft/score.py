"""Exact-curve scoring on the whitened spectrogram.

For a candidate point and a flight, the received frequency is f0 times a geometry-only ratio
c / (c + v_r(t)), so the ratio is computed once per candidate and flight on a fine time grid and
every (time shift, f0) combination in the sweep is a cheap re-sampling. The score of a curve is
the whitened brightness (dB above the per-frequency median) along it, averaged over frames with
weights (closest slant / slant)^p so the loud, steep stretch around closest approach counts most
and the faint far frames, where whatever else is playing can be picked up, count little, then
averaged over the harmonics with weights 1/n (the config's count or the event's, whichever is
larger). Frames whose sound the track does not cover count as zero brightness, so a leg that
holds only the few seconds around closest approach is not scored on its peak alone. Event 1
sweeps a small time correction and f0; that fixes the recording start, so event 2 sweeps f0
only. In single-event known-time mode the time sweep is the known-time
tolerance; a traced curve, when given, is reported as the RMS distance of the modeled curve
from it, a diagnostic that does not enter the score.

Each event is scored over a fixed window either side of its heard closest approach. Scoring
only the span its trace covers (`trace_window`, off by default) lifts a faint event's own score,
since a line traced for 20 s of a two-minute window is no longer averaged with the rest, but on
the M6 recordings it cost more than it gave: the scorer integrates along the predicted curve and
picks up line too faint to trace, a trace can stop short of the heard crossing itself, and the
untraced wings are what tell look-alike aircraft apart (docs/VALIDATION.md, M6).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from earwitness.aircraft.model import Track, closest_approach
from earwitness.audio.events import EventSet, FlyoverEvent
from earwitness.audio.spectrogram import Spectrogram
from earwitness.core.physics import SPEED_OF_SOUND, doppler_curve

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class ScoreConfig:
    """Scoring parameters."""

    window_s: float = 60.0
    """Frames within this many seconds of the measured closest approach are scored, for an
    event without a trace (see `trace_window`)."""
    trace_window: bool = False
    """Score a traced event over its trace's span widened by `trace_margin_s` instead; off by
    default, see the module notes."""
    trace_margin_s: float = 10.0
    time_sweep_s: float = 3.0
    time_step_s: float = 0.5
    f0_sweep_hz: float = 3.0
    f0_step_hz: float = 0.5
    harmonics: int = 1
    """Harmonics to score, weighted 1/n; an event's own harmonic count wins when it is larger."""
    distance_weight_power: float = 1.0
    """Frames are weighted by (closest slant / slant) to this power; 0 gives the plain mean."""
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
        # frame-major copy for the lookups: a curve's f0 options at one frame then sit side by
        # side in memory instead of a whole row of frames apart
        self._flat = np.ascontiguousarray(spec.whitened_db.T).ravel()

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
        nf = self.f.size
        col = np.clip(np.rint((t_s - self.t[0]) / self.dt).astype(np.int64), 0, self.t.size - 1)
        pos = (f_hz - self.f[0]) / self.df
        valid = (pos >= 0.0) & (pos <= nf - 1)  # false for NaN
        pos_c = np.clip(np.where(valid, pos, 0.0), 0.0, nf - 1 - 1e-9)
        lo = pos_c.astype(np.int64)
        frac = pos_c - lo
        base = np.broadcast_to(col * nf, pos.shape)
        i_lo = base + lo
        i_hi = np.minimum(i_lo + 1, base + nf - 1)
        values = self._flat.take(i_lo) * (1.0 - frac) + self._flat.take(i_hi) * frac
        return np.where(valid, values, np.nan)


def event_window(event: FlyoverEvent, cfg: ScoreConfig) -> tuple[float, float]:
    """Recording-time span scored for an event: its trace's span widened by the margin when it
    has a trace of five points or more (and `cfg.trace_window`), else `cfg.window_s` either
    side of its heard closest approach."""
    if cfg.trace_window and event.trace is not None and event.trace.shape[0] >= 5:
        t = event.trace[:, 0]
        t = t[np.isfinite(t)]
        if t.size >= 5:
            return float(t.min()) - cfg.trace_margin_s, float(t.max()) + cfg.trace_margin_s
    return event.t0_s.mid - cfg.window_s, event.t0_s.mid + cfg.window_s


def curve_geometry(
    track: Track, observer_xyz: FloatArray, t_abs: FloatArray, cfg: ScoreConfig
) -> tuple[FloatArray, FloatArray]:
    """Frequency ratio c / (c + v_r) and slant range at absolute reception times.

    The ratio is NaN where the sound would have left before the track's first sample or after
    its last: the data cannot say what the aircraft did there, so the scorer counts those
    frames as unexplained. The slant range is still returned there, from the track's
    straight-line continuation, so the frames keep their weight. Both are NaN more than a
    minute beyond the span.
    """
    t_abs = np.asarray(t_abs, dtype=float)
    t0, t1 = track.span
    inside = (t_abs >= t0 - 60.0) & (t_abs <= t1 + 60.0)
    ratio = np.full(t_abs.shape, np.nan)
    slant = np.full(t_abs.shape, np.nan)
    if inside.any():
        curve = doppler_curve(
            track.position,
            track.velocity,
            observer_xyz,
            t_abs[inside],
            1.0,
            c=cfg.speed_of_sound,
            iterations=cfg.iterations,
        )
        covered = (curve.t_e >= t0) & (curve.t_e <= t1)
        ratio[inside] = np.where(covered, curve.f, np.nan)
        slant[inside] = curve.r
    return ratio, slant


def frequency_ratio(
    track: Track, observer_xyz: FloatArray, t_abs: FloatArray, cfg: ScoreConfig
) -> FloatArray:
    """Received over rest frequency at absolute reception times; see `curve_geometry`."""
    return curve_geometry(track, observer_xyz, t_abs, cfg)[0]


def distance_weights(slant: FloatArray, cfg: ScoreConfig) -> FloatArray:
    """Per-frame weights (closest slant / slant)^p, NaN where the track does not cover the frame.

    The tone is loudest and its curve steepest near closest approach; far frames carry faint
    sound and whatever else is playing, so a plain mean lets a wrong curve collect brightness
    from other tones there.
    """
    finite = np.isfinite(slant)
    if cfg.distance_weight_power <= 0.0 or not finite.any():
        return np.where(finite, 1.0, np.nan)
    nearest = float(np.min(slant[finite]))
    power = cfg.distance_weight_power
    return np.where(finite, (nearest / np.maximum(slant, 1.0)) ** power, np.nan)


def weighted_mean(values: FloatArray, weights: FloatArray) -> FloatArray:
    """Weighted mean over the last axis, ignoring NaN values or weights; NaN when nothing counts."""
    ok = np.isfinite(values) & np.isfinite(weights)
    num = np.where(ok, values * weights, 0.0).sum(axis=-1)
    den = np.where(ok, weights, 0.0).sum(axis=-1)
    return np.where(den > 0.0, num / np.where(den > 0.0, den, 1.0), np.nan)


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
    t_rec = index.frames_between(*event_window(event, cfg))
    f0_half = max(cfg.f0_sweep_hz, event.f0_hz.half_width)
    f0s = np.arange(event.f0_hz.mid - f0_half, event.f0_hz.mid + f0_half + 1e-9, cfg.f0_step_hz)
    if t_rec.size == 0:
        return np.full((t_start_options.size, f0s.size), np.nan), t_start_options, f0s
    t_abs_lo = float(t_start_options.min() + t_rec[0] - cfg.ratio_step_s)
    t_abs_hi = float(t_start_options.max() + t_rec[-1] + cfg.ratio_step_s)
    grid = np.arange(t_abs_lo, t_abs_hi + cfg.ratio_step_s, cfg.ratio_step_s)
    ratio, slant = curve_geometry(track, observer_xyz, grid, cfg)
    scores = np.empty((t_start_options.size, f0s.size))
    n_harmonics = max(cfg.harmonics, event.harmonics, 1)
    weights = np.array([1.0 / n for n in range(1, n_harmonics + 1)])
    for i, t0 in enumerate(t_start_options):
        r = np.interp(t0 + t_rec, grid, ratio)
        frame_w = distance_weights(np.interp(t0 + t_rec, grid, slant), cfg)[None, :]
        total = np.zeros(f0s.size)
        for n, w in enumerate(weights, start=1):
            f = f0s[:, None] * (n * r[None, :])
            b = index.brightness(t_rec[None, :], f)
            # a frame the track does not cover explains nothing: zero brightness, full weight
            b = np.where(np.isnan(f) & np.isfinite(frame_w), 0.0, b)
            total += w * weighted_mean(b, frame_w)
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
    n_frames = index.frames_between(*event_window(event, cfg)).size
    return EventScore(float(scores[i, j]), float(f0s[j]), float(dts[i]), float(starts[i]), n_frames)


def trace_closeness(
    track: Track,
    observer_xyz: FloatArray,
    event: FlyoverEvent,
    t_start: float,
    f0: float,
    cfg: ScoreConfig,
) -> float:
    """Minus the RMS difference, Hz, between the modeled curve and the user's traced curve.

    Only trace points the track covers count; NaN with fewer than five of them. This is a
    diagnostic for the overlays, not a score: the brightness score is what the acceptance runs
    validated, and a trace is the user's reading of the same spectrogram.
    """
    if event.trace is None or event.trace.shape[0] == 0:
        return float("nan")
    t_rec = event.trace[:, 0]
    ratio = frequency_ratio(track, observer_xyz, t_start + t_rec, cfg)
    diff = f0 * ratio - event.trace[:, 1]
    ok = np.isfinite(diff)
    if ok.sum() < 5:
        return float("nan")
    return -float(np.sqrt(np.mean(diff[ok] ** 2)))


HEARD_PASS_SLACK_S = 10.0
"""Seconds added around an event's heard-time range when looking for the pass that was heard."""


def at_track_speed(
    event: FlyoverEvent,
    track: Track,
    observer_xyz: FloatArray,
    t_start: float | None = None,
    slack_s: float = HEARD_PASS_SLACK_S,
    c: float = SPEED_OF_SOUND,
) -> FlyoverEvent:
    """The event narrowed to the track's speed at closest approach on the pass that was heard.

    A profiled event ties distance, fundamental and closest-approach time to the speed; the
    ADS-B track supplies the speed, so the sweeps stay narrow. With the recording start
    `t_start`, the closest approach is sought among the track times whose sound arrives inside
    the event's heard-time range, widened by `slack_s`: an aircraft working a pattern passes a
    point several times at different speeds, and the nearest pass need not be the one heard.
    Without it, the nearest pass of the whole track. Events without a profile are returned as
    they are.
    """
    if not event.profiled:
        return event
    window = None
    if t_start is not None:
        window = (t_start + event.t0_s.lo - slack_s, t_start + event.t0_s.hi + slack_s)
    t_cpa, _ = closest_approach(track, observer_xyz, heard_window=window, c=c)
    step = 0.5
    v = float(np.linalg.norm(track.position(t_cpa + step) - track.position(t_cpa - step))) / (
        2.0 * step
    )
    return event.narrowed(v)


@dataclass(frozen=True)
class CandidateScore:
    """Scores of one candidate point."""

    total: float
    events: tuple[EventScore, ...]
    t_start: float
    trace_rms_hz: float = float("nan")


def time_sweep_for(
    e1: FlyoverEvent,
    cfg: ScoreConfig,
    time_sweep_s: float | None = None,
    known_time_tolerance_s: float | None = None,
) -> float:
    """Half-width of event 1's time sweep, seconds.

    Known-time mode sweeps the clock tolerance plus the event's closest-approach width, taken
    from the event as narrowed to the flight's speed: the envelope of a profiled trace can be
    tens of seconds wide, and sweeping that would let a candidate stretch a clock the user
    trusts to a second. Otherwise an explicit sweep, or the configured one widened to the
    event's width.
    """
    if known_time_tolerance_s is not None:
        return known_time_tolerance_s + e1.t0_s.half_width
    if time_sweep_s is None:
        return max(cfg.time_sweep_s, e1.t0_s.half_width)
    return time_sweep_s


def score_candidate(
    index: SpectrogramIndex,
    tracks: list[Track],
    observer_xyz: FloatArray,
    events: EventSet,
    t_start_base: float,
    cfg: ScoreConfig,
    time_sweep_s: float | None = None,
    known_time_tolerance_s: float | None = None,
) -> CandidateScore:
    """Locked multi-event score: event 1 sweeps time and f0, later events sweep f0 only.

    Profiled events are narrowed to each flight's speed on the pass heard during the event,
    found around the candidate's start (`at_track_speed`); in known-time mode the clock
    tolerance widens that search for event 1.
    """
    c = cfg.speed_of_sound
    slack = HEARD_PASS_SLACK_S + (known_time_tolerance_s or 0.0)
    e1 = at_track_speed(events.events[0], tracks[0], observer_xyz, t_start_base, slack, c)
    sweep = time_sweep_for(e1, cfg, time_sweep_s, known_time_tolerance_s)
    first = score_event(index, tracks[0], observer_xyz, e1, t_start_base, cfg, sweep)
    results = [first]
    t_start = first.t_start
    for event, track in zip(events.events[1:], tracks[1:], strict=True):
        narrowed = at_track_speed(event, track, observer_xyz, t_start, HEARD_PASS_SLACK_S, c)
        results.append(score_event(index, track, observer_xyz, narrowed, t_start, cfg, None))
    total = float(sum(r.score for r in results))
    trace_rms = float("nan")
    if e1.trace is not None:
        trace_rms = -trace_closeness(tracks[0], observer_xyz, e1, t_start, first.f0_hz, cfg)
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
