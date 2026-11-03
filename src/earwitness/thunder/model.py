"""Thunder forward model: when, and how loudly, a candidate point hears each strike.

For every strike the model gives an arrival time from the 3D slant distance, a loudness from
inverse-power falloff with a near-field cap, and a pulse kernel shaped like thunder (fast attack,
exponential decay that lengthens with distance). The sum of the pulses, smoothed like the observed
envelope, is the modeled envelope that the search scores against the recording.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.ndimage import gaussian_filter1d

from earwitness.core import geodesy
from earwitness.core.physics import SPEED_OF_SOUND

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class ThunderConfig:
    """Tunable constants. Values marked "per video" come from colsto's thunder videos."""

    speed_of_sound: float = SPEED_OF_SOUND
    source_height_m: float = 2000.0
    """Acoustic source height for cloud-to-ground strikes, m (per video)."""
    source_height_ic_m: float = 7000.0
    """Source height for in-cloud flashes, m. Assumption; the handoff gives no number."""
    falloff_exponent: float = 2.0
    """p in (r_near / r)^p (per video). Note p = 1 is spherical spreading of pressure."""
    near_field_m: float = 1000.0
    """Loudness saturates inside this slant range, like a clipped microphone."""
    rise_s: float = 0.5
    """Pulse kernel attack time constant."""
    decay_s: float = 3.5
    """Pulse kernel decay time constant at zero range."""
    decay_per_km_s: float = 0.1
    """Extra decay per kilometer of slant range: distant thunder rumbles longer."""
    smoothing_sigma_s: float = 5.0
    """Gaussian smoothing sigma applied to both observed and modeled envelopes (per video UI)."""


@dataclass(frozen=True)
class StrikeSet:
    """Lightning events with times in one common base (seconds) and WGS84 positions."""

    time_s: FloatArray
    lat: FloatArray
    lon: FloatArray
    kind: NDArray[np.str_] | None = None
    """Per-strike "CG" or "IC". None means all cloud-to-ground."""

    def __len__(self) -> int:
        return int(self.time_s.shape[0])


@dataclass(frozen=True)
class StrikeGeometry:
    """Per-strike quantities as heard from one observer."""

    horizontal_m: FloatArray
    slant_m: FloatArray
    arrival_s: FloatArray
    amplitude: FloatArray
    decay_s: FloatArray


def source_heights(kind: NDArray[np.str_] | None, n: int, cfg: ThunderConfig) -> FloatArray:
    """Source height per strike from its kind."""
    if kind is None:
        return np.full(n, cfg.source_height_m)
    return np.where(np.asarray(kind) == "IC", cfg.source_height_ic_m, cfg.source_height_m)


def slant_distance(horizontal_m: ArrayLike, height_m: ArrayLike) -> FloatArray:
    """3D distance from a ground observer to a source at horizontal distance and height."""
    return np.hypot(np.asarray(horizontal_m, dtype=float), np.asarray(height_m, dtype=float))


def arrival_time(
    t_strike: ArrayLike, horizontal_m: ArrayLike, height_m: ArrayLike, c: float = SPEED_OF_SOUND
) -> FloatArray:
    """t_arr = t_strike + sqrt(d_h^2 + h^2) / c."""
    return np.asarray(t_strike, dtype=float) + slant_distance(horizontal_m, height_m) / c


def loudness(
    slant_m: ArrayLike, falloff_exponent: float = 2.0, near_field_m: float = 1000.0
) -> FloatArray:
    """Relative loudness (r_near / max(r, r_near))^p: 1 inside the near field, falling beyond.

    Equivalent to the handoff's min(A_cap, (R_0 / R)^p) with R_0 = r_near and A_cap = 1. The
    score is scale-invariant, so only the near-field radius carries information.
    """
    r = np.maximum(np.asarray(slant_m, dtype=float), near_field_m)
    return (near_field_m / r) ** falloff_exponent


def decay_time(slant_m: ArrayLike, cfg: ThunderConfig) -> FloatArray:
    """Kernel decay constant at a given slant range, s."""
    return cfg.decay_s + cfg.decay_per_km_s * np.asarray(slant_m, dtype=float) / 1000.0


def kernel_peak_time(rise_s: float, decay_s: ArrayLike) -> FloatArray:
    """Time after arrival at which the pulse kernel peaks: rise * ln(1 + decay / rise)."""
    return rise_s * np.log1p(np.asarray(decay_s, dtype=float) / rise_s)


def pulse_kernel(t: ArrayLike, rise_s: float, decay_s: ArrayLike) -> FloatArray:
    """Fast-attack, exponential-decay pulse normalized to a peak of 1; zero before t = 0.

    `decay_s` may be a scalar or an array broadcastable against `t` (one decay per strike).
    """
    dec = np.asarray(decay_s, dtype=float)
    if rise_s <= 0.0 or np.any(dec <= 0.0):
        raise ValueError("rise_s and decay_s must be positive")
    tt = np.asarray(t, dtype=float)
    pos = np.clip(tt, 0.0, None)
    shape = (1.0 - np.exp(-pos / rise_s)) * np.exp(-pos / dec)
    t_peak = kernel_peak_time(rise_s, dec)
    peak = (1.0 - np.exp(-t_peak / rise_s)) * np.exp(-t_peak / dec)
    return np.where(tt >= 0.0, shape / peak, 0.0)


def kernel_support(rise_s: float, decay_s: ArrayLike) -> FloatArray:
    """Duration after arrival beyond which the kernel is below about 3e-4 of its peak."""
    return kernel_peak_time(rise_s, decay_s) + 8.0 * np.asarray(decay_s, dtype=float)


def render_envelope(
    t: FloatArray,
    arrival_s: ArrayLike,
    amplitude: ArrayLike,
    rise_s: float,
    decay_s: ArrayLike,
) -> FloatArray:
    """Sum of amplitude-scaled pulse kernels on a uniform time grid, unsmoothed."""
    t = np.asarray(t, dtype=float)
    arrivals = np.atleast_1d(np.asarray(arrival_s, dtype=float))
    amps = np.broadcast_to(np.asarray(amplitude, dtype=float), arrivals.shape)
    decays = np.broadcast_to(np.asarray(decay_s, dtype=float), arrivals.shape)
    supports = kernel_support(rise_s, decays)
    m = np.zeros_like(t)
    for t_arr, amp, dec, sup in zip(arrivals, amps, decays, supports, strict=True):
        i0 = int(np.searchsorted(t, t_arr, side="left"))
        i1 = int(np.searchsorted(t, t_arr + sup, side="right"))
        if i1 > i0:
            m[i0:i1] += amp * pulse_kernel(t[i0:i1] - t_arr, rise_s, float(dec))
    return m


def smooth(values: FloatArray, step_s: float, sigma_s: float) -> FloatArray:
    """Gaussian smoothing with sigma given in seconds; identity when sigma is zero."""
    if sigma_s <= 0.0:
        return np.asarray(values, dtype=float).copy()
    return gaussian_filter1d(np.asarray(values, dtype=float), sigma_s / step_s, mode="nearest")


def strike_geometry(
    strikes: StrikeSet, observer_lat: float, observer_lon: float, cfg: ThunderConfig
) -> StrikeGeometry:
    """Distances, arrival times, loudness and decay of every strike for one observer."""
    horizontal = geodesy.horizontal_distance(strikes.lat, strikes.lon, observer_lat, observer_lon)
    heights = source_heights(strikes.kind, len(strikes), cfg)
    slant = slant_distance(horizontal, heights)
    return StrikeGeometry(
        horizontal_m=horizontal,
        slant_m=slant,
        arrival_s=strikes.time_s + slant / cfg.speed_of_sound,
        amplitude=loudness(slant, cfg.falloff_exponent, cfg.near_field_m),
        decay_s=decay_time(slant, cfg),
    )


def model_envelope(
    t: FloatArray,
    strikes: StrikeSet,
    observer_lat: float,
    observer_lon: float,
    cfg: ThunderConfig | None = None,
    smooth_output: bool = True,
) -> FloatArray:
    """Modeled loudness envelope at one candidate point on a uniform time grid.

    `t` is in the same time base as `strikes.time_s`. The result is in arbitrary units; the
    search compares it to the observed envelope with cosine similarity.
    """
    cfg = cfg or ThunderConfig()
    t = np.asarray(t, dtype=float)
    geom = strike_geometry(strikes, observer_lat, observer_lon, cfg)
    m = render_envelope(t, geom.arrival_s, geom.amplitude, cfg.rise_s, geom.decay_s)
    if smooth_output and t.size > 1:
        m = smooth(m, float(t[1] - t[0]), cfg.smoothing_sigma_s)
    return m
