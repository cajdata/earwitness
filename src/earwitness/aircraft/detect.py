"""Automatic flyover measurement: find propeller Doppler lines and fit them without clicks.

The handoff left automatic detection as a stretch goal behind the UI's sliders and trace tool.
This module does what an analyst does with those tools, in order:

1. **Peaks.** Each frame of the whitened spectrogram gives its local maxima in frequency that
   stand `peak_db` above the per-frequency median, the strongest `max_peaks` of them.
2. **Tracks.** Peaks link frame to frame, each track taking the free peak nearest its predicted
   frequency (last frequency plus its recent slope), so crossing lines keep their own tracks.
3. **Candidates.** Tracks long enough that fall like a flyover seed a fit.
4. **Fit and follow.** The straight-flyover fit (`aircraft.fit`) of a track predicts the whole
   curve; the peaks nearest that curve, in runs that last (a line is there; scattered noise
   peaks near a flat wing are not), are fitted again, a few times. That is the analyst sliding
   the curve onto the line and tracing along it, out to the wings the tracker lost.
5. **Gate.** Fits no propeller aircraft could produce (speed, distance, how fast the curve
   falls, residual) are dropped. Cars pass in seconds; aircraft take tens of seconds.
6. **Group.** Lines heard crossing at the same moment, or following one curve scaled, are one
   aircraft: harmonics of the blade passage and the engine. The strongest aircraft goes first
   and claims the peaks along its lines; every weaker one is measured again without them, so a
   faint flyover cannot borrow a louder one's harmonics.

Each flyover keeps its clearest line, from which `to_event` builds the `events.json` entry with
the speed profile (`fit.profile_speed`), exactly as a hand-traced event is built. On the two M6
recordings this finds the flyovers measured by hand there, within the fits' own uncertainty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from earwitness.aircraft.fit import (
    PROFILE_TOLERANCE_HZ,
    DopplerFit,
    fit_straight_flyover,
    preview_curve,
)
from earwitness.audio.events import EventSet, FlyoverEvent, Range
from earwitness.audio.spectrogram import Spectrogram, spectrogram
from earwitness.core.physics import SPEED_OF_SOUND

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]

RATE_HZ = 4000
NFFT = 4096
HOP = 512
"""Spectrogram the detector measures on: about 1 Hz bins and 0.128 s frames at 4 kHz, the
resolution the M6 measurements used."""


@dataclass(frozen=True)
class DetectConfig:
    """Thresholds of the automatic measurement; the defaults are the M6-validated ones."""

    f_lo_hz: float = 40.0
    f_hi_hz: float = 600.0
    """Band searched for lines: propeller fundamentals and their first harmonics."""
    peak_db: float = 6.0
    """Whitened dB a spectral peak must reach."""
    max_peaks: int = 12
    """Strongest peaks kept per frame."""
    link_rel: float = 0.006
    link_bins: float = 1.5
    """A track takes a peak within this fraction of its predicted frequency, or this many bins,
    whichever is larger (widened for each missed frame)."""
    max_gap_s: float = 1.5
    """A track ends after this long without a peak."""
    young_points: int = 6
    young_gap_frames: int = 2
    """Tracks shorter than `young_points` end after `young_gap_frames` missed frames, so noise
    peaks do not keep thousands of tracks alive."""
    min_track_s: float = 6.0
    min_drop: float = 0.02
    """A candidate track lasts this long and falls by at least this fraction."""
    guide_rel: float = 0.012
    guide_bins: float = 1.5
    """Half-width of the band around a fitted curve that its next trace takes peaks from."""
    guide_iterations: int = 4
    guide_tau: float = 4.0
    guide_pad_s: float = 20.0
    guide_max_half_s: float = 240.0
    """The traced window is the heard crossing plus or minus guide_tau * d / v + guide_pad_s,
    at most guide_max_half_s: wide enough for the wings, which set the speed."""
    min_run_s: float = 1.5
    max_run_gap_frames: int = 3
    """Peaks near the curve count only in runs lasting `min_run_s` (gaps up to
    `max_run_gap_frames`): scattered noise near a flat wing would pin a speed the recording
    cannot show."""
    core: tuple[float, float] = (0.05, 0.95)
    """Share of the Doppler drop, from the approach asymptote (0) to the recede one (1), within
    which a trace takes peaks. The flat far wings run alongside steady tones (engines, hum,
    road noise), and a few such peaks pin a speed and distance the curve cannot show, so the
    ranges exclude the real aircraft; the falling part is what a hand trace covers too, and
    the speed profile then states how little it says about the speed."""
    v_range: tuple[float, float] = (25.0, 160.0)
    d_range: tuple[float, float] = (150.0, 12000.0)
    min_tau_s: float = 4.0
    """Speed, closest-approach distance and d / v a measured flyover must have: road traffic
    passes within seconds and tens of metres."""
    rms_floor_hz: float = 1.2
    rms_rel: float = 0.006
    """A fit's rms residual must stay under the larger of these (Hz, fraction of f0)."""
    min_points: int = 40
    """Trace points a measured line needs."""
    edge_s: float = 30.0
    """A flyover heard closer than this to either end of the clip is used as an event only
    when too few others are found: half its curve lies outside the clip, so its fit leans on
    one wing (in the synthetic cases one such pick, 19 s into a clip, sent the answer to
    another flight)."""
    valley_rms_factor: float = 1.0
    """The event's speed profile keeps speeds whose fit is within the larger of 0.5 Hz
    (`fit.PROFILE_TOLERANCE_HZ`) and this many times the trace's rms of the best fit."""


@dataclass(frozen=True)
class Peaks:
    """Every kept spectral peak, sorted by frame and then by descending brightness."""

    frame: IntArray
    f: FloatArray
    db: FloatArray
    starts: IntArray
    """Frame j's peaks are [starts[j], starts[j + 1])."""
    t: FloatArray
    df: float


def frame_peaks(spec: Spectrogram, cfg: DetectConfig) -> Peaks:
    """Local maxima over two bins either side, bright enough, sub-bin interpolated."""
    rows = np.flatnonzero((spec.f >= cfg.f_lo_hz) & (spec.f <= cfg.f_hi_hz))
    if rows.size < 5:
        raise ValueError("the spectrogram does not cover the detection band")
    w = spec.whitened_db[rows]
    n_t = w.shape[1]
    m = np.ones(w.shape, dtype=bool)
    for k in (1, 2):
        m[k:] &= w[k:] > w[:-k]
        m[:-k] &= w[:-k] >= w[k:]
    m[:2] = False
    m[-2:] = False
    m &= w >= cfg.peak_db
    fi, ti = np.nonzero(m)
    y0, y1, y2 = w[fi - 1, ti], w[fi, ti], w[fi + 1, ti]
    denom = y0 - 2.0 * y1 + y2
    safe = np.where(np.abs(denom) > 1e-12, denom, 1.0)
    delta = np.where(np.abs(denom) > 1e-12, 0.5 * (y0 - y2) / safe, 0.0)
    df = float(spec.f[1] - spec.f[0])
    freq = spec.f[rows][fi] + np.clip(delta, -0.5, 0.5) * df
    order = np.lexsort((-y1, ti))
    ti, freq, db = ti[order], freq[order], y1[order]
    starts = np.searchsorted(ti, np.arange(n_t + 1))
    keep = (np.arange(ti.size) - starts[ti]) < cfg.max_peaks
    ti, freq, db = ti[keep], freq[keep], db[keep]
    starts = np.searchsorted(ti, np.arange(n_t + 1))
    return Peaks(frame=ti.astype(np.int64), f=freq, db=db, starts=starts, t=spec.t, df=df)


@dataclass
class Track:
    """Peaks linked frame to frame: indices into `Peaks`, their frames and frequencies."""

    idx: list[int] = field(default_factory=list)
    frames: list[int] = field(default_factory=list)
    fs: list[float] = field(default_factory=list)
    slope: float = 0.0
    """Recent slope, Hz/s, over up to eight frames."""


def link_tracks(peaks: Peaks, cfg: DetectConfig) -> list[Track]:
    """Link peaks into tracks; longer tracks choose first, each its nearest free peak."""
    t = peaks.t
    dt = float(t[1] - t[0]) if t.size > 1 else 1.0
    max_gap = max(1, round(cfg.max_gap_s / dt))
    active: list[Track] = []
    done: list[Track] = []
    for j in range(t.size):
        a, b = int(peaks.starts[j]), int(peaks.starts[j + 1])
        pf = peaks.f[a:b]
        if active:
            gaps = np.array([j - tr.frames[-1] for tr in active])
            n_pts = np.array([len(tr.frames) for tr in active])
            alive = (gaps <= max_gap) & (
                (n_pts >= cfg.young_points) | (gaps <= cfg.young_gap_frames)
            )
            done.extend(tr for tr, ok in zip(active, alive, strict=True) if not ok)
            active = [tr for tr, ok in zip(active, alive, strict=True) if ok]
        used = np.zeros(pf.size, dtype=bool)
        if active and pf.size:
            last_f = np.array([tr.fs[-1] for tr in active])
            slope = np.array([tr.slope for tr in active])
            gap = np.array([j - tr.frames[-1] for tr in active], dtype=float)
            pred = last_f + slope * gap * dt
            tol = np.maximum(cfg.link_bins * peaks.df, cfg.link_rel * pred) * (
                1.0 + 0.25 * (gap - 1.0)
            )
            dist = np.abs(pf[None, :] - pred[:, None])
            ok = dist <= tol[:, None]
            n_pts = np.array([len(tr.frames) for tr in active])
            for i in np.argsort(-n_pts, kind="stable"):
                free = np.flatnonzero(ok[i] & ~used)
                if free.size == 0:
                    continue
                k = int(free[np.argmin(dist[i, free])])
                used[k] = True
                tr = active[i]
                tr.idx.append(a + k)
                tr.frames.append(j)
                tr.fs.append(float(pf[k]))
                back = min(len(tr.fs) - 1, 8)
                if back >= 4:
                    span = (tr.frames[-1] - tr.frames[-1 - back]) * dt
                    tr.slope = (tr.fs[-1] - tr.fs[-1 - back]) / span
        active.extend(
            Track(idx=[a + int(k)], frames=[j], fs=[float(pf[k])]) for k in np.flatnonzero(~used)
        )
    done.extend(active)
    return done


def candidate_tracks(tracks: list[Track], peaks: Peaks, cfg: DetectConfig) -> list[Track]:
    """Tracks long and dense enough that fall by at least `min_drop` from start to end."""
    dt = float(peaks.t[1] - peaks.t[0]) if peaks.t.size > 1 else 1.0
    out = []
    for tr in tracks:
        if len(tr.frames) < 8:
            continue
        span = (tr.frames[-1] - tr.frames[0]) * dt
        if span < cfg.min_track_s or len(tr.frames) < 0.4 * span / dt:
            continue
        fs = np.asarray(tr.fs)
        q = max(2, fs.size // 6)
        f_start, f_end = float(np.median(fs[:q])), float(np.median(fs[-q:]))
        if (f_start - f_end) / max(0.5 * (f_start + f_end), 1e-6) >= cfg.min_drop:
            out.append(tr)
    return out


def runs_at_least(frames: IntArray, min_frames: int, max_gap: int) -> BoolArray:
    """Mask of sorted frame numbers that belong to runs spanning at least `min_frames`."""
    keep = np.zeros(frames.size, dtype=bool)
    if frames.size == 0:
        return keep
    breaks = np.flatnonzero(np.diff(frames) > max_gap)
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks + 1, frames.size]
    for s, e in zip(starts, ends, strict=True):
        if frames[e - 1] - frames[s] + 1 >= min_frames:
            keep[s:e] = True
    return keep


def guided_points(
    peaks: Peaks, fit: DopplerFit, cfg: DetectConfig, excluded: BoolArray | None = None
) -> tuple[FloatArray, FloatArray, FloatArray, IntArray]:
    """(t, f, dB, peak index) of the peaks along a fitted curve: the one nearest the curve in
    each frame inside the traced window, kept only where they run on (`runs_at_least`)."""
    t = peaks.t
    empty = (np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0, dtype=np.int64))
    heard = fit.heard_t0_s()
    half = min(
        cfg.guide_max_half_s, cfg.guide_tau * fit.d_m / max(fit.v_mps, 1.0) + cfg.guide_pad_s
    )
    j0 = int(np.searchsorted(t, heard - half))
    j1 = int(np.searchsorted(t, heard + half))
    if j1 <= j0:
        return empty
    a, b = int(peaks.starts[j0]), int(peaks.starts[j1])
    idx = np.arange(a, b)
    if excluded is not None:
        idx = idx[~excluded[a:b]]
    fr = peaks.frame[idx]
    pred = preview_curve(t[j0:j1], fit.f0_hz, fit.v_mps, fit.d_m, fit.t0_s)[fr - j0]
    f_app = fit.f0_hz * SPEED_OF_SOUND / (SPEED_OF_SOUND - fit.v_mps)
    f_rec = fit.f0_hz * SPEED_OF_SOUND / (SPEED_OF_SOUND + fit.v_mps)
    progress = (f_app - pred) / max(f_app - f_rec, 1e-9)
    dev = np.abs(peaks.f[idx] - pred)
    ok = dev <= np.maximum(cfg.guide_bins * peaks.df, cfg.guide_rel * pred)
    ok &= (progress >= cfg.core[0]) & (progress <= cfg.core[1])
    idx, fr, dev = idx[ok], fr[ok], dev[ok]
    order = np.lexsort((dev, fr))
    idx, fr = idx[order], fr[order]
    first = np.ones(fr.size, dtype=bool)
    first[1:] = fr[1:] != fr[:-1]
    idx, fr = idx[first], fr[first]
    dt = float(t[1] - t[0]) if t.size > 1 else 1.0
    idx = idx[runs_at_least(fr, max(1, round(cfg.min_run_s / dt)), cfg.max_run_gap_frames)]
    return t[peaks.frame[idx]], peaks.f[idx], peaks.db[idx], idx


def plausible(fit: DopplerFit, t: FloatArray, cfg: DetectConfig, loose: bool = False) -> bool:
    """Whether a fit could be a propeller flyover heard in this trace.

    `loose` checks only that the fit is physical, for a first fit to a raw track that may hold
    stray peaks from a crossing line; the guided traces that follow it get the full gate.
    """
    v_lo, v_hi = (10.0, 250.0) if loose else cfg.v_range
    d_lo, d_hi = (20.0, 30000.0) if loose else cfg.d_range
    if not (v_lo <= fit.v_mps <= v_hi and d_lo <= fit.d_m <= d_hi):
        return False
    if not loose:
        if fit.rms_hz > max(cfg.rms_floor_hz, cfg.rms_rel * fit.f0_hz):
            return False
        if fit.d_m / fit.v_mps < cfg.min_tau_s:
            return False
    heard = fit.heard_t0_s()
    span = float(t.max() - t.min())
    return float(t.min()) - 0.5 * span <= heard <= float(t.max()) + 0.5 * span


@dataclass
class DetectedLine:
    """One measured line (a harmonic) of a flyover: its fit and its guided trace."""

    fit: DopplerFit
    t_s: FloatArray
    f_hz: FloatArray
    db: FloatArray
    idx: IntArray
    quality: float
    """Brightness above the peak threshold summed along the trace, half as much again when
    the trace covers both sides of the crossing."""
    track: Track

    @property
    def heard_s(self) -> float:
        """When the line crosses its f0, seconds into the recording."""
        return self.fit.heard_t0_s()


def measure_track(
    peaks: Peaks, tr: Track, cfg: DetectConfig, excluded: BoolArray | None = None
) -> DetectedLine | None:
    """Fit a track, follow the fitted curve and refit until the trace settles; None if the
    result is not a plausible flyover."""
    idx0 = np.asarray(tr.idx, dtype=np.int64)
    if excluded is not None:
        idx0 = idx0[~excluded[idx0]]
    if idx0.size < 8:
        return None
    t, f = peaks.t[peaks.frame[idx0]], peaks.f[idx0]
    try:
        fit = fit_straight_flyover(t, f, profile=False)
    except ValueError:
        return None
    if not plausible(fit, t, cfg, loose=True):
        return None
    pts = (t, f, peaks.db[idx0], idx0)
    for _ in range(cfg.guide_iterations):
        gt, gf, gdb, gidx = guided_points(peaks, fit, cfg, excluded)
        if gt.size < cfg.min_points:
            break
        try:
            new = fit_straight_flyover(
                gt, gf, initial=[fit.f0_hz, fit.v_mps, fit.d_m, fit.t0_s], profile=False
            )
        except ValueError:
            break
        if not plausible(new, gt, cfg, loose=True):
            break
        settled = np.array_equal(gidx, pts[3])
        fit, pts = new, (gt, gf, gdb, gidx)
        if settled:
            break
    t, f, db, idx = pts
    if t.size < cfg.min_points or not plausible(fit, t, cfg):
        return None
    heard = fit.heard_t0_s()
    before = int((t < heard).sum())
    both = min(before, t.size - before) >= 0.15 * t.size
    quality = float(np.sum(np.clip(db - cfg.peak_db + 1.0, 0.0, None))) * (1.5 if both else 1.0)
    return DetectedLine(fit=fit, t_s=t, f_hz=f, db=db, idx=idx, quality=quality, track=tr)


def follows(rep: DetectedLine, other: DetectedLine, tol: float = 0.01, share: float = 0.5) -> bool:
    """True when most of `other`'s trace lies on one scaled copy of `rep`'s fitted curve."""
    g = preview_curve(other.t_s, 1.0, rep.fit.v_mps, rep.fit.d_m, rep.fit.t0_s)
    r = other.f_hz / g
    k = float(np.median(r))
    return bool(np.mean(np.abs(r / k - 1.0) < tol) >= share)


def same_aircraft(rep: DetectedLine, other: DetectedLine) -> bool:
    """Lines crossing within max(8 s, 0.4 d / v) of each other, or on one scaled curve."""
    tau = min(rep.fit.d_m / rep.fit.v_mps, other.fit.d_m / other.fit.v_mps)
    if abs(rep.heard_s - other.heard_s) <= max(8.0, 0.4 * tau):
        return True
    return follows(rep, other)


@dataclass
class DetectedFlyover:
    """One aircraft: its clearest line, the other lines heard with it, and their quality."""

    line: DetectedLine
    others: list[DetectedLine]
    harmonics: int
    """Consecutive harmonics of the clearest line among the others (1, 2 or 3): what the
    scorer should sum."""
    quality: float
    """Summed over every line."""

    @property
    def heard_s(self) -> float:
        """Heard closest-approach crossing of the clearest line, seconds into the recording."""
        return self.line.heard_s

    def to_event(
        self, event_id: str, valley_rms_factor: float = DetectConfig.valley_rms_factor
    ) -> FlyoverEvent:
        """The `events.json` entry, built as a hand-traced event is: a profiled fit's ranges
        and speed valley over the clearest line's trace."""
        f = self.line.fit
        tolerance = max(PROFILE_TOLERANCE_HZ, valley_rms_factor * f.rms_hz)
        fit = fit_straight_flyover(
            self.line.t_s,
            self.line.f_hz,
            initial=[f.f0_hz, f.v_mps, f.d_m, f.t0_s],
            profile=True,
            tolerance_hz=tolerance,
        )
        r = fit.ranges()
        rows = np.asarray(fit.heard_valley(), dtype=float)
        return FlyoverEvent(
            id=event_id,
            t0_s=Range(*r["t0_s"]),
            f0_hz=Range(*r["f0_hz"]),
            v_mps=Range(*r["v_mps"]),
            d_m=Range(*r["d_m"]),
            harmonics=self.harmonics,
            trace=np.column_stack([self.line.t_s, self.line.f_hz]),
            valley=rows[:, :4] if rows.size else None,
        )

    def summary(self) -> dict[str, object]:
        """JSON-ready description for the CLI and the UI."""
        f = self.line.fit
        return {
            "heard_s": round(self.heard_s, 2),
            "f0_hz": round(f.f0_hz, 2),
            "v_mps": round(f.v_mps, 1),
            "d_m": round(f.d_m, 0),
            "rms_hz": round(f.rms_hz, 3),
            "start_s": round(float(self.line.t_s.min()), 1),
            "end_s": round(float(self.line.t_s.max()), 1),
            "points": int(self.line.t_s.size),
            "lines_hz": sorted(round(x.fit.f0_hz, 1) for x in [self.line, *self.others]),
            "harmonics": self.harmonics,
            "quality": round(self.quality, 1),
        }


def make_flyover(lines: list[DetectedLine]) -> DetectedFlyover:
    """A flyover from its lines, the first being the clearest."""
    rep, others = lines[0], lines[1:]
    harmonics = 1
    for k in (2, 3):
        if any(abs(o.fit.f0_hz / (k * rep.fit.f0_hz) - 1.0) < 0.02 for o in others):
            harmonics = k
        else:
            break
    return DetectedFlyover(
        line=rep, others=others, harmonics=harmonics, quality=sum(x.quality for x in lines)
    )


def group_lines(lines: list[DetectedLine]) -> list[DetectedFlyover]:
    """Group lines by aircraft, clearest line first in each; strongest aircraft first."""
    groups: list[list[DetectedLine]] = []
    for line in sorted(lines, key=lambda x: -x.quality):
        for g in groups:
            if same_aircraft(g[0], line):
                g.append(line)
                break
        else:
            groups.append([line])
    return sorted((make_flyover(g) for g in groups), key=lambda x: -x.quality)


@dataclass
class Detection:
    """Flyovers found in a recording, strongest first, with counts from each stage."""

    flyovers: list[DetectedFlyover]
    counts: dict[str, int]
    duration_s: float = 0.0
    """Length of the spectrogram the flyovers were measured on; 0 when unknown."""
    edge_s: float = DetectConfig.edge_s

    def usable(self) -> list[DetectedFlyover]:
        """Flyovers heard at least `edge_s` inside the clip, strongest first."""
        if self.duration_s <= 0.0:
            return list(self.flyovers)
        lo, hi = self.edge_s, self.duration_s - self.edge_s
        return [f for f in self.flyovers if lo <= f.heard_s <= hi]


def detect_flyovers(spec: Spectrogram, cfg: DetectConfig | None = None) -> Detection:
    """Find and measure every propeller flyover in a whitened spectrogram."""
    cfg = cfg or DetectConfig()
    peaks = frame_peaks(spec, cfg)
    tracks = link_tracks(peaks, cfg)
    seeds = candidate_tracks(tracks, peaks, cfg)
    lines = [m for m in (measure_track(peaks, tr, cfg) for tr in seeds) if m is not None]
    first = group_lines(lines)
    owned = np.zeros(peaks.f.size, dtype=bool)
    final: list[DetectedFlyover] = []
    for fly in first:
        members = [fly.line, *fly.others]
        if owned.any():
            again = [
                m
                for m in (measure_track(peaks, x.track, cfg, owned) for x in members)
                if m is not None
            ]
            if not again:
                continue
            again.sort(key=lambda x: -x.quality)
            fly = make_flyover(again)
            members = [fly.line, *fly.others]
        final.append(fly)
        for m in members:
            owned[m.idx] = True
            owned[guided_points(peaks, m.fit, cfg)[3]] = True
    final.sort(key=lambda x: -x.quality)
    counts = {
        "peaks": int(peaks.f.size),
        "tracks": len(tracks),
        "candidates": len(seeds),
        "lines": len(lines),
        "flyovers": len(final),
    }
    duration = float(spec.t[-1]) if spec.t.size else 0.0
    return Detection(flyovers=final, counts=counts, duration_s=duration, edge_s=cfg.edge_s)


def detection_spectrogram(samples: FloatArray, rate: int, cfg: DetectConfig) -> Spectrogram:
    """The detector's spectrogram of mono samples already at `RATE_HZ`."""
    if rate != RATE_HZ:
        raise ValueError(f"resample to {RATE_HZ} Hz first")
    return spectrogram(samples, rate, nfft=NFFT, hop=HOP, fmax=cfg.f_hi_hz + 20.0)


def auto_events(
    detection: Detection,
    max_events: int = 2,
    known_time: datetime | None = None,
    known_time_tolerance_s: float = 2.0,
    recording: dict[str, object] | None = None,
    valley_rms_factor: float = DetectConfig.valley_rms_factor,
) -> EventSet | None:
    """The strongest `max_events` flyovers as an event set in time order, ids auto1, auto2...

    Flyovers heard well inside the clip come first (`Detection.usable`); one near an end is
    taken only when too few others are found. Two flyovers give the two-event search, which a
    known time also constrains; one flyover needs the known time (single-event mode). None when
    nothing usable was found.
    """
    inside = detection.usable()
    if known_time is not None:
        # a clock makes one flyover enough: rather fewer events than one cut in half
        chosen = inside[:max_events] or detection.flyovers[:1]
    else:
        chosen = inside[:max_events]
        if len(chosen) < max_events:
            rest = [f for f in detection.flyovers if f not in chosen]
            chosen = chosen + rest[: max_events - len(chosen)]
    if not chosen or (len(chosen) == 1 and known_time is None):
        return None
    chosen = sorted(chosen, key=lambda x: x.heard_s)
    events = [fly.to_event(f"auto{k}", valley_rms_factor) for k, fly in enumerate(chosen, start=1)]
    return EventSet(
        events=events,
        known_time=known_time,
        known_time_tolerance_s=known_time_tolerance_s,
        recording=dict(recording or {}),
    )
