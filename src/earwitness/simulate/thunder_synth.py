"""Synthetic thunder: band-limited noise bursts shaped by the forward model's own kernel.

Every strike becomes a burst of 20 to 250 Hz noise whose amplitude envelope is the pulse kernel
from `earwitness.thunder.model`, scaled by the modeled loudness and placed at the modeled arrival
time, so the RMS envelope of the synthetic recording is the forward model by construction. Close
strikes add a short broadband crack and saturate softly like a clipped microphone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from numpy.typing import NDArray
from scipy.signal import butter, sosfiltfilt

from earwitness.core import geodesy
from earwitness.simulate.mix import rms, soft_clip
from earwitness.thunder.model import (
    StrikeGeometry,
    StrikeSet,
    ThunderConfig,
    kernel_support,
    pulse_kernel,
    strike_geometry,
)

FloatArray = NDArray[np.float64]

CRACK_RANGE_M = 5000.0
CRACK_DECAY_S = 0.15
CRACK_GAIN = 0.5
SOFT_CLIP_LEVEL = 2.0


@dataclass(frozen=True)
class Strike:
    """One lightning event; `time_s` is seconds relative to the recording start."""

    time_s: float
    lat: float
    lon: float
    kind: str = "CG"


@dataclass
class ThunderScenario:
    """Everything needed to render a thunder recording with a known answer."""

    observer_lat: float
    observer_lon: float
    observer_elevation_m: float
    start_time: datetime
    duration_s: float
    strikes: list[Strike]
    config: ThunderConfig = field(default_factory=ThunderConfig)

    def __post_init__(self) -> None:
        if self.start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware (UTC)")
        self.start_time = self.start_time.astimezone(UTC)
        if self.duration_s <= 0:
            raise ValueError("duration_s must be positive")

    def strike_set(self) -> StrikeSet:
        """Strikes as arrays in recording-relative seconds."""
        return StrikeSet(
            time_s=np.array([s.time_s for s in self.strikes], dtype=float),
            lat=np.array([s.lat for s in self.strikes], dtype=float),
            lon=np.array([s.lon for s in self.strikes], dtype=float),
            kind=np.array([s.kind for s in self.strikes], dtype=str),
        )

    def geometry(self) -> StrikeGeometry:
        """Per-strike distances, arrival times and loudness at the observer."""
        return strike_geometry(self.strike_set(), self.observer_lat, self.observer_lon, self.config)


def random_storm(
    rng: np.random.Generator,
    observer_lat: float,
    observer_lon: float,
    duration_s: float,
    n_strikes: int,
    center_bearing_deg: float,
    center_distance_m: float,
    radius_m: float,
    drift_speed_mps: float = 0.0,
    drift_bearing_deg: float = 0.0,
    ic_fraction: float = 0.0,
    lead_s: float = 60.0,
) -> list[Strike]:
    """Strikes from a drifting storm cell: Gaussian scatter (sigma = radius / 2) around a center.

    Times are uniform between `-lead_s` (so thunder can already be rolling at the start) and the
    end of the clip. A fraction `ic_fraction` are marked in-cloud.
    """
    times = np.sort(rng.uniform(-lead_s, duration_s, n_strikes))
    b = np.deg2rad(center_bearing_deg)
    db = np.deg2rad(drift_bearing_deg)
    center0 = center_distance_m * np.array([np.sin(b), np.cos(b)])
    drift = drift_speed_mps * np.array([np.sin(db), np.cos(db)])
    offsets = rng.normal(0.0, radius_m / 2.0, size=(n_strikes, 2))
    horiz = center0[None, :] + drift[None, :] * times[:, None] + offsets
    enu = np.column_stack([horiz, np.zeros(n_strikes)])
    lat, lon, _ = geodesy.enu_to_geodetic(enu, observer_lat, observer_lon, 0.0)
    kinds = np.where(rng.uniform(size=n_strikes) < ic_fraction, "IC", "CG")
    return [
        Strike(float(t), float(la), float(lo), str(k))
        for t, la, lo, k in zip(times, lat, lon, kinds, strict=True)
    ]


def synthesize_thunder(
    scenario: ThunderScenario,
    rate: int,
    rng: np.random.Generator,
    crack: bool = True,
    clip: bool = True,
) -> FloatArray:
    """Render the clean thunder signal (no background noise) for a scenario."""
    cfg = scenario.config
    geom = scenario.geometry()
    n = round(scenario.duration_s * rate)
    out = np.zeros(n)
    lo, hi = 20.0, min(250.0, 0.45 * rate)
    sos = butter(4, [lo, hi], btype="bandpass", fs=rate, output="sos")
    supports = kernel_support(cfg.rise_s, geom.decay_s)
    for t_arr, amp, decay, support, slant in zip(
        geom.arrival_s, geom.amplitude, geom.decay_s, supports, geom.slant_m, strict=True
    ):
        start = round(t_arr * rate)
        length = int(np.ceil(support * rate))
        if start >= n or start + length <= 0 or length <= 0:
            continue
        tau = np.arange(length) / rate
        noise = sosfiltfilt(sos, rng.standard_normal(length))
        noise /= rms(noise)
        burst = amp * pulse_kernel(tau, cfg.rise_s, float(decay)) * noise
        if crack and slant < CRACK_RANGE_M:
            weight = CRACK_GAIN * (1.0 - slant / CRACK_RANGE_M)
            burst += amp * weight * np.exp(-tau / CRACK_DECAY_S) * rng.standard_normal(length)
        i0, i1 = max(start, 0), min(start + length, n)
        out[i0:i1] += burst[i0 - start : i1 - start]
    if clip:
        out = soft_clip(out, SOFT_CLIP_LEVEL)
    return out
