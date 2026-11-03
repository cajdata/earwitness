"""Thunder search: sliding cosine scoring of observed against modeled envelopes over a grid.

For a batch of candidate points the model envelope is rendered on one time axis that spans every
start-time offset in the prior range plus the clip length. The numerator of the cosine score for
all offsets comes from one FFT cross-correlation per candidate, the denominator from a cumulative
sum of the squared model, so each candidate costs O(n log n) in the number of offsets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.fft import irfft, next_fast_len, rfft
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree

from earwitness.core import geodesy
from earwitness.data.adapters import LightningEvents
from earwitness.thunder.model import (
    ThunderConfig,
    decay_time,
    kernel_support,
    loudness,
    pulse_kernel,
    slant_distance,
    source_heights,
)

FloatArray = NDArray[np.float64]
Progress = Callable[[str], None] | None


@dataclass(frozen=True)
class SearchConfig:
    """Search parameters (defaults per the handoff)."""

    step_s: float = 5.0
    """Offset sweep step; also the model sample step."""
    r_max_m: float = 25000.0
    """Audible radius: strikes farther than this are not modeled."""
    min_strikes: int | None = None
    """Prefilter N; None means max(3, round(0.6 * observed peak count))."""
    top_offsets: int = 3
    batch_size: int = 256
    bearing_weight: float = 0.0
    """0 disables bearing-diversity weighting; 1 fully flattens the bearing histogram."""
    bearing_sectors: int = 8
    score_mode: str = "cosine"
    """"cosine" (per the handoff) or "pearson" (window mean removed, see docs/METHOD.md)."""


@dataclass
class CandidateResults:
    """Per-candidate best score and offsets. Offsets are Unix UTC seconds of the clip start."""

    lat: FloatArray
    lon: FloatArray
    score: FloatArray
    offset_unix: FloatArray
    top_offsets_unix: FloatArray
    top_scores: FloatArray
    n_strikes: NDArray[np.int64]
    stage: str = ""

    def __len__(self) -> int:
        return int(self.lat.shape[0])

    def order(self) -> NDArray[np.int64]:
        """Indices sorted by descending score."""
        return np.argsort(-self.score, kind="stable")

    def subset(self, idx: NDArray[np.int64] | NDArray[np.bool_]) -> CandidateResults:
        """Rows selected by index or mask."""
        return CandidateResults(
            self.lat[idx],
            self.lon[idx],
            self.score[idx],
            self.offset_unix[idx],
            self.top_offsets_unix[idx],
            self.top_scores[idx],
            self.n_strikes[idx],
            self.stage,
        )

    @staticmethod
    def concat(parts: list[CandidateResults], stage: str = "") -> CandidateResults:
        """Stack results."""
        if not parts:
            raise ValueError("nothing to concatenate")
        return CandidateResults(
            np.concatenate([p.lat for p in parts]),
            np.concatenate([p.lon for p in parts]),
            np.concatenate([p.score for p in parts]),
            np.concatenate([p.offset_unix for p in parts]),
            np.concatenate([p.top_offsets_unix for p in parts]),
            np.concatenate([p.top_scores for p in parts]),
            np.concatenate([p.n_strikes for p in parts]),
            stage or parts[0].stage,
        )

    @staticmethod
    def empty(stage: str = "") -> CandidateResults:
        """No candidates."""
        z = np.zeros(0)
        return CandidateResults(
            z, z, z, z, np.zeros((0, 1)), np.zeros((0, 1)), np.zeros(0, dtype=np.int64), stage
        )


def cosine_similarity(observed: ArrayLike, modeled: ArrayLike) -> float:
    """Cosine of the angle between two envelopes of equal length; 0 if either is all zero."""
    o = np.asarray(observed, dtype=float)
    m = np.asarray(modeled, dtype=float)
    if o.shape != m.shape:
        raise ValueError(f"shape mismatch: {o.shape} vs {m.shape}")
    denom = np.linalg.norm(o) * np.linalg.norm(m)
    if denom == 0.0:
        return 0.0
    return float(np.dot(o, m) / denom)


def sliding_cosine(observed: ArrayLike, models: ArrayLike) -> FloatArray:
    """Cosine score of `observed` (K,) against every length-K window of each model row (B, T).

    Returns shape (B, T - K + 1); column tau scores the window starting at model sample tau.
    Windows with no model energy score 0.
    """
    o = np.asarray(observed, dtype=float)
    m = np.atleast_2d(np.asarray(models, dtype=float))
    k, t = o.size, m.shape[1]
    if k == 0 or k > t:
        raise ValueError(f"observed length {k} must be between 1 and model length {t}")
    n = t - k + 1
    norm_o = float(np.linalg.norm(o))
    if norm_o == 0.0:
        return np.zeros((m.shape[0], n))
    nfft = next_fast_len(t + k - 1)
    corr = irfft(rfft(m, nfft, axis=1) * np.conj(rfft(o, nfft)), nfft, axis=1)[:, :n]
    csum = np.concatenate([np.zeros((m.shape[0], 1)), np.cumsum(m * m, axis=1)], axis=1)
    energy = csum[:, k : k + n] - csum[:, :n]
    denom = norm_o * np.sqrt(np.maximum(energy, 0.0))
    valid = energy > 1e-18
    scores = np.zeros_like(corr)
    np.divide(corr, denom, out=scores, where=valid)
    return np.clip(scores, -1.0, 1.0)


def sliding_pearson(observed: ArrayLike, models: ArrayLike) -> FloatArray:
    """Correlation coefficient of `observed` against every length-K window of each model row.

    Like `sliding_cosine` but with the mean removed from the observed clip and from each model
    window, so a shared loudness floor (a continuous rumble) earns no credit and only the timing
    structure counts. Windows with no model variance score 0.
    """
    o = np.asarray(observed, dtype=float)
    m = np.atleast_2d(np.asarray(models, dtype=float))
    k, t = o.size, m.shape[1]
    if k == 0 or k > t:
        raise ValueError(f"observed length {k} must be between 1 and model length {t}")
    n = t - k + 1
    o = o - o.mean()
    norm_o = float(np.linalg.norm(o))
    if norm_o == 0.0:
        return np.zeros((m.shape[0], n))
    nfft = next_fast_len(t + k - 1)
    corr = irfft(rfft(m, nfft, axis=1) * np.conj(rfft(o, nfft)), nfft, axis=1)[:, :n]
    zeros = np.zeros((m.shape[0], 1))
    c1 = np.concatenate([zeros, np.cumsum(m, axis=1)], axis=1)
    c2 = np.concatenate([zeros, np.cumsum(m * m, axis=1)], axis=1)
    s1 = c1[:, k : k + n] - c1[:, :n]
    s2 = c2[:, k : k + n] - c2[:, :n]
    var = s2 - s1 * s1 / k
    denom = norm_o * np.sqrt(np.maximum(var, 0.0))
    valid = var > 1e-18
    scores = np.zeros_like(corr)
    np.divide(corr, denom, out=scores, where=valid)
    return np.clip(scores, -1.0, 1.0)


def sliding_score(observed: ArrayLike, models: ArrayLike, mode: str = "cosine") -> FloatArray:
    """Dispatch on `SearchConfig.score_mode`."""
    if mode == "cosine":
        return sliding_cosine(observed, models)
    if mode == "pearson":
        return sliding_pearson(observed, models)
    raise ValueError(f"unknown score mode {mode!r}; use 'cosine' or 'pearson'")


class StrikeIndex:
    """Spatial index over lightning events for radius queries (chord distance in ECEF)."""

    def __init__(self, events: LightningEvents) -> None:
        self.events = events
        self.xyz = geodesy.geodetic_to_ecef(events.lat, events.lon, 0.0).reshape(-1, 3)
        self.tree = cKDTree(self.xyz) if len(events) else None

    def within(self, lat: ArrayLike, lon: ArrayLike, r_m: float) -> list[list[int]]:
        """Event indices within `r_m` of each query point."""
        pts = geodesy.geodetic_to_ecef(lat, lon, 0.0).reshape(-1, 3)
        if self.tree is None:
            return [[] for _ in range(pts.shape[0])]
        return list(self.tree.query_ball_point(pts, r_m))

    def counts(self, lat: ArrayLike, lon: ArrayLike, r_m: float) -> NDArray[np.int64]:
        """Number of events within `r_m` of each query point."""
        pts = geodesy.geodetic_to_ecef(lat, lon, 0.0).reshape(-1, 3)
        if self.tree is None:
            return np.zeros(pts.shape[0], dtype=np.int64)
        return np.asarray(self.tree.query_ball_point(pts, r_m, return_length=True), dtype=np.int64)


def max_window_count(times_sorted: FloatArray, window_s: float) -> int:
    """Largest number of sorted times falling inside any window of length `window_s`."""
    t = np.asarray(times_sorted, dtype=float)
    if t.size == 0:
        return 0
    right = np.searchsorted(t, t + window_s, side="right")
    return int(np.max(right - np.arange(t.size)))


def strike_count_prefilter(
    index: StrikeIndex,
    lat: FloatArray,
    lon: FloatArray,
    r_max_m: float,
    window_s: float,
    chunk: int = 20000,
) -> NDArray[np.int64]:
    """Per point: the most strikes within `r_max_m` that fall in any window of `window_s`."""
    out = np.zeros(lat.size, dtype=np.int64)
    for start in range(0, lat.size, chunk):
        stop = min(start + chunk, lat.size)
        counts = index.counts(lat[start:stop], lon[start:stop], r_max_m)
        busy = np.flatnonzero(counts > 0)
        if busy.size == 0:
            continue
        lists = index.within(lat[start:stop][busy], lon[start:stop][busy], r_max_m)
        times = index.events.time_unix
        for b, idx in zip(busy, lists, strict=True):
            out[start + b] = max_window_count(
                np.sort(times[np.asarray(idx, dtype=np.int64)]), window_s
            )
    return out


def bearing_weights(bearings_deg: FloatArray, sectors: int, strength: float) -> FloatArray:
    """Weights that boost strikes in sparsely populated bearing sectors.

    With strength 1 every sector contributes equally regardless of how many strikes it holds;
    with strength 0 all weights are 1. Weights are normalized to a mean of 1.
    """
    b = np.asarray(bearings_deg, dtype=float)
    if b.size == 0 or strength <= 0.0:
        return np.ones(b.size)
    sector = (np.floor(np.mod(b, 360.0) / (360.0 / sectors)).astype(int)) % sectors
    counts = np.bincount(sector, minlength=sectors).astype(float)
    mean_count = b.size / max(np.count_nonzero(counts), 1)
    w = (mean_count / counts[sector]) ** strength
    return w / w.mean()


def render_models(
    index: StrikeIndex,
    lat: FloatArray,
    lon: FloatArray,
    t0_unix: float,
    step_s: float,
    n_samples: int,
    model_cfg: ThunderConfig,
    search_cfg: SearchConfig,
) -> tuple[FloatArray, NDArray[np.int64]]:
    """Modeled envelopes (B, n_samples) on the axis t0 + j * step for a batch of candidates."""
    lat = np.atleast_1d(np.asarray(lat, dtype=float))
    lon = np.atleast_1d(np.asarray(lon, dtype=float))
    out = np.zeros((lat.size, n_samples))
    n_strikes = np.zeros(lat.size, dtype=np.int64)
    ev = index.events
    lists = index.within(lat, lon, search_cfg.r_max_m)
    max_decay = float(decay_time(search_cfg.r_max_m, model_cfg))
    k_max = int(np.ceil(float(kernel_support(model_cfg.rise_s, max_decay)) / step_s)) + 1
    for b, idx_list in enumerate(lists):
        if not idx_list:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        n_strikes[b] = idx.size
        enu = geodesy.geodetic_to_enu(ev.lat[idx], ev.lon[idx], 0.0, lat[b], lon[b], 0.0)
        horizontal = np.hypot(enu[:, 0], enu[:, 1])
        kinds = None if ev.kind is None else ev.kind[idx]
        slant = slant_distance(horizontal, source_heights(kinds, idx.size, model_cfg))
        arrival = ev.time_unix[idx] + slant / model_cfg.speed_of_sound
        amp = loudness(slant, model_cfg.falloff_exponent, model_cfg.near_field_m)
        if search_cfg.bearing_weight > 0.0:
            bearings = np.rad2deg(np.arctan2(enu[:, 0], enu[:, 1]))
            amp = amp * bearing_weights(
                bearings, search_cfg.bearing_sectors, search_cfg.bearing_weight
            )
        decay = decay_time(slant, model_cfg)
        rel = arrival - t0_unix
        j0 = np.ceil(rel / step_s).astype(np.int64)
        row = out[b]
        for k in range(k_max):
            j = j0 + k
            valid = (j >= 0) & (j < n_samples)
            if not np.any(valid):
                continue
            tau = j[valid] * step_s - rel[valid]
            vals = amp[valid] * pulse_kernel(tau, model_cfg.rise_s, decay[valid])
            row += np.bincount(j[valid], weights=vals, minlength=n_samples)
    if model_cfg.smoothing_sigma_s > 0.0:
        out = gaussian_filter1d(out, model_cfg.smoothing_sigma_s / step_s, axis=1, mode="nearest")
    return out, n_strikes


def search(
    observed: FloatArray,
    index: StrikeIndex,
    lat: FloatArray,
    lon: FloatArray,
    t_start_unix: float,
    t_end_unix: float,
    search_cfg: SearchConfig | None = None,
    model_cfg: ThunderConfig | None = None,
    stage: str = "",
    progress: Progress = None,
) -> CandidateResults:
    """Score every candidate at every clip-start offset in [t_start, t_end] at the sweep step.

    `observed` is the observed envelope already resampled to `search_cfg.step_s`.
    """
    search_cfg = search_cfg or SearchConfig()
    model_cfg = model_cfg or ThunderConfig()
    lat = np.atleast_1d(np.asarray(lat, dtype=float))
    lon = np.atleast_1d(np.asarray(lon, dtype=float))
    o = np.asarray(observed, dtype=float)
    k = o.size
    step = search_cfg.step_s
    n_offsets = int(np.floor((t_end_unix - t_start_unix) / step + 1e-9)) + 1
    if n_offsets < 1:
        raise ValueError("t_end must not be before t_start")
    n_samples = n_offsets + k - 1
    n_top = max(1, min(search_cfg.top_offsets, n_offsets))
    parts: list[CandidateResults] = []
    total = lat.size
    for start in range(0, total, search_cfg.batch_size):
        stop = min(start + search_cfg.batch_size, total)
        models, n_strikes = render_models(
            index,
            lat[start:stop],
            lon[start:stop],
            t_start_unix,
            step,
            n_samples,
            model_cfg,
            search_cfg,
        )
        scores = sliding_score(o, models, search_cfg.score_mode)
        top_idx = np.argpartition(-scores, n_top - 1, axis=1)[:, :n_top]
        top_sc = np.take_along_axis(scores, top_idx, axis=1)
        order = np.argsort(-top_sc, axis=1, kind="stable")
        top_idx = np.take_along_axis(top_idx, order, axis=1)
        top_sc = np.take_along_axis(top_sc, order, axis=1)
        parts.append(
            CandidateResults(
                lat=lat[start:stop],
                lon=lon[start:stop],
                score=top_sc[:, 0],
                offset_unix=t_start_unix + top_idx[:, 0] * step,
                top_offsets_unix=t_start_unix + top_idx * step,
                top_scores=top_sc,
                n_strikes=n_strikes,
                stage=stage,
            )
        )
        if progress and (stop == total or (stop // search_cfg.batch_size) % 20 == 0):
            progress(f"{stage or 'search'}: {stop}/{total} candidates scored")
    if not parts:
        return CandidateResults.empty(stage)
    return CandidateResults.concat(parts, stage)
