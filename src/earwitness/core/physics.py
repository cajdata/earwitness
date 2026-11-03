"""Acoustic physics shared by both methods: sound speed, Doppler shift, emission time.

Sign convention: radial velocity is the source's speed along the line from the observer to the
source, negative while approaching, so an approaching source is heard above its rest frequency.
Positions are meters in any Cartesian frame (the engine uses local ENU), times are seconds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]
PositionFn = Callable[[FloatArray], FloatArray]
"""Maps an array of times (...,) to positions (..., 3)."""

SPEED_OF_SOUND = 343.0
"""Default speed of sound, m/s, dry air near 20 C. The handoff's worked Doppler example uses it."""


def speed_of_sound(temperature_c: float = 20.0) -> float:
    """Speed of sound in dry air, m/s: 331.3 * sqrt(1 + T / 273.15)."""
    return 331.3 * float(np.sqrt(1.0 + temperature_c / 273.15))


def doppler_frequency(f0: ArrayLike, v_r: ArrayLike, c: float = SPEED_OF_SOUND) -> FloatArray:
    """Received frequency for a moving source and a stationary observer: f0 * c / (c + v_r)."""
    return np.asarray(f0, dtype=float) * c / (c + np.asarray(v_r, dtype=float))


def radial_velocity_from_frequency(
    f: ArrayLike, f0: ArrayLike, c: float = SPEED_OF_SOUND
) -> FloatArray:
    """Inverse of `doppler_frequency`: v_r = c * (f0 / f - 1)."""
    return c * (np.asarray(f0, dtype=float) / np.asarray(f, dtype=float) - 1.0)


def radial_velocity(position: ArrayLike, velocity: ArrayLike, observer: ArrayLike) -> FloatArray:
    """Component of the source velocity along the observer-to-source line, m/s."""
    rel = np.asarray(position, dtype=float) - np.asarray(observer, dtype=float)
    r = np.linalg.norm(rel, axis=-1)
    return np.sum(np.asarray(velocity, dtype=float) * rel, axis=-1) / r


@dataclass(frozen=True)
class DopplerCurve:
    """A received-frequency curve with the geometry behind each sample."""

    t_r: FloatArray
    """Reception time, s."""
    t_e: FloatArray
    """Emission time, s."""
    r: FloatArray
    """Source-observer distance at emission, m."""
    v_r: FloatArray
    """Radial velocity at emission, m/s, negative while approaching."""
    f: FloatArray
    """Received frequency, Hz."""


def straight_line_emission_time(
    t_r: ArrayLike, v: float, d: float, t0: float, c: float = SPEED_OF_SOUND
) -> FloatArray:
    """Emission time for a straight constant-velocity flyover, solved in closed form.

    With tau = t_e - t0 and T = t_r - t0, the reception equation
    c * (T - tau) = sqrt(d^2 + v^2 tau^2) is quadratic in tau; the root with tau < T is
    tau = (c^2 T - sqrt(c^2 v^2 T^2 + d^2 (c^2 - v^2))) / (c^2 - v^2).
    """
    big_t = np.asarray(t_r, dtype=float) - t0
    c2 = c * c
    v2 = v * v
    tau = (c2 * big_t - np.sqrt(c2 * v2 * big_t * big_t + d * d * (c2 - v2))) / (c2 - v2)
    return t0 + tau


def straight_line_doppler(
    t_r: ArrayLike, f0: float, v: float, d: float, t0: float, c: float = SPEED_OF_SOUND
) -> DopplerCurve:
    """Doppler curve of a straight constant-velocity flyover, exact in reception time.

    r(t_e) = sqrt(d^2 + v^2 (t_e - t0)^2), v_r = v^2 (t_e - t0) / r, t_r = t_e + r / c.
    This is the measurement model fitted to spectrogram curves and the reference the iterative
    solver is tested against.
    """
    t_r_arr = np.asarray(t_r, dtype=float)
    t_e = straight_line_emission_time(t_r_arr, v, d, t0, c)
    tau = t_e - t0
    r = np.sqrt(d * d + v * v * tau * tau)
    v_r = v * v * tau / r
    return DopplerCurve(t_r=t_r_arr, t_e=t_e, r=r, v_r=v_r, f=doppler_frequency(f0, v_r, c))


def solve_emission_time(
    position: PositionFn,
    observer: ArrayLike,
    t_r: ArrayLike,
    c: float = SPEED_OF_SOUND,
    iterations: int = 5,
) -> FloatArray:
    """Emission time for each reception time by fixed-point iteration.

    Starting from t_e = t_r, repeat t_e <- t_r - |p(t_e) - o| / c. Each pass shrinks the error by
    |v_r| / c, about 0.3 for a propeller aircraft, so five passes leave well under 1% of the
    acoustic delay as residual (see tests for measured numbers). Jets at 250 m/s converge much
    more slowly; the aircraft funnel drops them before this runs.
    """
    t_r_arr = np.asarray(t_r, dtype=float)
    o = np.asarray(observer, dtype=float)
    t_e = t_r_arr.copy()
    for _ in range(iterations):
        t_e = t_r_arr - np.linalg.norm(position(t_e) - o, axis=-1) / c
    return t_e


def doppler_curve(
    position: PositionFn,
    velocity: PositionFn,
    observer: ArrayLike,
    t_r: ArrayLike,
    f0: float,
    c: float = SPEED_OF_SOUND,
    iterations: int = 5,
    use_emission_time: bool = True,
) -> DopplerCurve:
    """Received-frequency curve for an arbitrary source path, evaluated at reception times.

    `use_emission_time=False` evaluates the path at reception time instead. That is the wrong
    model; it exists so tests can show the two curves differ in shape, not just in delay.
    """
    t_r_arr = np.asarray(t_r, dtype=float)
    o = np.asarray(observer, dtype=float)
    if use_emission_time:
        t_e = solve_emission_time(position, o, t_r_arr, c, iterations)
    else:
        t_e = t_r_arr.copy()
    rel = position(t_e) - o
    r = np.linalg.norm(rel, axis=-1)
    v_r = np.sum(velocity(t_e) * rel, axis=-1) / r
    return DopplerCurve(t_r=t_r_arr, t_e=t_e, r=r, v_r=v_r, f=doppler_frequency(f0, v_r, c))
