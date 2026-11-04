"""Aircraft forward model: interpolated tracks and the exact Doppler curve a point would hear."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.interpolate import CubicSpline
from scipy.optimize import minimize_scalar

from earwitness.core import geodesy
from earwitness.core.physics import SPEED_OF_SOUND, DopplerCurve
from earwitness.core.physics import doppler_curve as _doppler_curve

FloatArray = NDArray[np.float64]


def uniform_times(t0: float, t1: float, step_s: float) -> FloatArray:
    """t0, t0 + step, ... through t1 when it lies on the grid; safe for Unix-scale times."""
    n = int(np.floor((t1 - t0) / step_s + 1e-6)) + 1
    return t0 + np.arange(n) * step_s


class Track:
    """An aircraft path in local ENU meters with a smooth interpolant for position and velocity.

    Times are seconds in whatever base the caller uses (recording-relative for synthetic cases,
    Unix seconds for real traces). Positions are relative to a reference point, normally the
    candidate observer or a regional origin. A cubic spline gives continuous velocity between the
    1 s ADS-B samples. Beyond the sampled span the path continues in a straight line at the end
    sample's velocity, so emission times slightly before the first sample are still defined and
    never run off a cubic extrapolation.
    """

    def __init__(self, t: ArrayLike, enu: ArrayLike, icao: str = "", type_code: str = "") -> None:
        self.t = np.asarray(t, dtype=float)
        self.enu = np.asarray(enu, dtype=float)
        self.icao = icao
        self.type_code = type_code
        if self.t.ndim != 1 or self.t.size < 2:
            raise ValueError("a track needs at least two time samples")
        if self.enu.shape != (self.t.size, 3):
            raise ValueError(f"enu must have shape ({self.t.size}, 3), got {self.enu.shape}")
        if np.any(np.diff(self.t) <= 0.0):
            raise ValueError("track times must be strictly increasing")
        self._spline = CubicSpline(self.t, self.enu, axis=0, extrapolate=True)
        self._velocity = self._spline.derivative()

    @property
    def span(self) -> tuple[float, float]:
        """First and last sample time."""
        return float(self.t[0]), float(self.t[-1])

    def position(self, t: ArrayLike) -> FloatArray:
        """ENU position at the given times, shape (..., 3); straight-line beyond the span."""
        t = np.asarray(t, dtype=float)
        t0, t1 = self.span
        pos = self._spline(np.clip(t, t0, t1))
        before = t < t0
        after = t > t1
        if before.any():
            pos = pos + np.where(before[..., None], (t - t0)[..., None] * self._velocity(t0), 0.0)
        if after.any():
            pos = pos + np.where(after[..., None], (t - t1)[..., None] * self._velocity(t1), 0.0)
        return pos

    def velocity(self, t: ArrayLike) -> FloatArray:
        """ENU velocity at the given times, shape (..., 3), m/s; constant beyond the span."""
        t = np.asarray(t, dtype=float)
        t0, t1 = self.span
        return self._velocity(np.clip(t, t0, t1))

    def speed(self, t: ArrayLike) -> FloatArray:
        """Speed at the given times, m/s."""
        return np.linalg.norm(self.velocity(t), axis=-1)

    def resample(self, step_s: float = 1.0) -> Track:
        """Resample to a uniform step over the original span."""
        t0, t1 = self.span
        t = uniform_times(t0, t1, step_s)
        return Track(t, self.position(t), icao=self.icao, type_code=self.type_code)

    @classmethod
    def from_geodetic(
        cls,
        t: ArrayLike,
        lat_deg: ArrayLike,
        lon_deg: ArrayLike,
        h_m: ArrayLike,
        ref_lat: float,
        ref_lon: float,
        ref_h: float = 0.0,
        icao: str = "",
        type_code: str = "",
    ) -> Track:
        """Build from geodetic samples (ellipsoidal heights) about a reference point."""
        enu = geodesy.geodetic_to_enu(lat_deg, lon_deg, h_m, ref_lat, ref_lon, ref_h)
        return cls(t, enu, icao=icao, type_code=type_code)

    def to_geodetic(
        self, ref_lat: float, ref_lon: float, ref_h: float = 0.0
    ) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Geodetic (lat, lon, ellipsoidal h) of each sample, given the frame's reference point."""
        return geodesy.enu_to_geodetic(self.enu, ref_lat, ref_lon, ref_h)


def straight_track(
    speed_mps: float,
    heading_deg: float,
    height_m: float,
    lateral_offset_m: float,
    t_cpa_s: float,
    t_start: float,
    t_end: float,
    step_s: float = 1.0,
    icao: str = "",
    type_code: str = "",
) -> Track:
    """Level, straight, constant-speed track about an observer at the ENU origin.

    The aircraft flies along `heading_deg` at `height_m` above the origin and passes closest
    at `t_cpa_s`, offset `lateral_offset_m` to the right of its heading. Closest-approach slant
    distance is sqrt(lateral^2 + height^2).
    """
    if t_end <= t_start:
        raise ValueError("t_end must be after t_start")
    theta = np.deg2rad(heading_deg)
    along = np.array([np.sin(theta), np.cos(theta), 0.0])
    right = np.array([np.cos(theta), -np.sin(theta), 0.0])
    t = np.arange(t_start, t_end + 1e-9, step_s)
    base = lateral_offset_m * right + np.array([0.0, 0.0, height_m])
    enu = base[None, :] + speed_mps * (t - t_cpa_s)[:, None] * along[None, :]
    return Track(t, enu, icao=icao, type_code=type_code)


def closest_approach(track: Track, observer: ArrayLike = (0.0, 0.0, 0.0)) -> tuple[float, float]:
    """Time and slant distance of closest approach within the track's span."""
    o = np.asarray(observer, dtype=float)
    dist = np.linalg.norm(track.enu - o, axis=1)
    i = int(np.argmin(dist))
    lo = float(track.t[max(i - 1, 0)])
    hi = float(track.t[min(i + 1, track.t.size - 1)])
    if hi <= lo:
        return float(track.t[i]), float(dist[i])
    # Optimize in seconds relative to the nearest sample: the bounded method's tolerance scales
    # with the magnitude of x, which would be tens of seconds for Unix times.
    t_ref = float(track.t[i])
    res = minimize_scalar(
        lambda tau: float(np.linalg.norm(track.position(t_ref + tau) - o)),
        bounds=(lo - t_ref, hi - t_ref),
        method="bounded",
        options={"xatol": 1e-7},
    )
    return float(t_ref + res.x), float(res.fun)


def heard_cpa_time(track: Track, observer: ArrayLike, c: float = SPEED_OF_SOUND) -> float:
    """Reception time at which the closest-approach emission arrives (frequency crosses f0)."""
    t_cpa, slant = closest_approach(track, observer)
    return t_cpa + slant / c


def doppler_curve(
    track: Track,
    observer: ArrayLike,
    t_r: ArrayLike,
    f0: float,
    c: float = SPEED_OF_SOUND,
    iterations: int = 5,
    use_emission_time: bool = True,
) -> DopplerCurve:
    """Exact received-frequency curve of a track as heard at `observer` (ENU meters)."""
    return _doppler_curve(
        track.position,
        track.velocity,
        observer,
        t_r,
        f0,
        c=c,
        iterations=iterations,
        use_emission_time=use_emission_time,
    )
