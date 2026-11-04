"""Straight-line Doppler fit: (f0, v, d, t0) from traced spectrogram points.

This is the UI's fit tool. The user traces a curve, or the ridge finder does, and the fit turns
the points into the ranges `events.json` wants. The model is the closed-form straight flyover of
`core.physics.straight_line_doppler`, solved by bounded least squares in frequency (soft-L1 loss,
so stray points do not drag it) with the speed kept below the speed of sound and the distance
positive. Ranges come from the fit's covariance
widened by floors: a short trace fixes f0 and t0 well but trades v against d (both set how
steeply the curve falls), and the funnel wants honest ranges rather than a point.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.optimize import least_squares

from earwitness.core.physics import SPEED_OF_SOUND, straight_line_doppler

FloatArray = NDArray[np.float64]

PARAMS = ("f0_hz", "v_mps", "d_m", "t0_s")
MIN_POINTS = 5


@dataclass(frozen=True)
class DopplerFit:
    """Fitted straight-flyover parameters with their one-sigma uncertainties."""

    f0_hz: float
    v_mps: float
    d_m: float
    t0_s: float
    sigma: dict[str, float]
    """One-sigma uncertainty per parameter; infinite when the trace does not resolve it."""
    rms_hz: float
    n_points: int

    def ranges(
        self,
        k: float = 3.0,
        floors: tuple[float, float, float, float] = (3.0, 10.0, 100.0, 3.0),
        relative: tuple[float, float] = (0.3, 0.5),
        c: float = SPEED_OF_SOUND,
    ) -> dict[str, list[float]]:
        """`events.json` ranges: each parameter plus or minus the largest of k sigma, a floor
        and (for speed and distance) a fraction of the value, kept physical.

        The floors and fractions are the widths the M4 acceptance runs used (3 s, 3 Hz, 30% of
        the speed, 50% of the distance): a straight-line fit to a real, curving track is biased,
        and the funnel would rather search a little wider than miss the flight. Unresolved
        parameters (infinite sigma) get wider ranges still.
        """
        values = (self.f0_hz, self.v_mps, self.d_m, self.t0_s)
        half: list[float] = []
        for name, value, floor in zip(PARAMS, values, floors, strict=True):
            s = self.sigma.get(name, float("inf"))
            fraction = relative[0] if name == "v_mps" else relative[1] if name == "d_m" else 0.0
            if not np.isfinite(s):
                if name == "v_mps":
                    half.append(max(value - 30.0, 120.0 - value, floor))
                elif name == "d_m":
                    half.append(max(value * 0.9, floor))
                else:
                    half.append(floor * 3.0)
            else:
                half.append(max(k * s, floor, fraction * abs(value)))
        f0 = [max(self.f0_hz - half[0], 10.0), self.f0_hz + half[0]]
        v = [max(self.v_mps - half[1], 5.0), min(self.v_mps + half[1], 0.9 * c)]
        d = [max(self.d_m - half[2], 20.0), min(self.d_m + half[2], 50000.0)]
        t0 = [self.t0_s - half[3], self.t0_s + half[3]]
        return {"f0_hz": f0, "v_mps": v, "d_m": d, "t0_s": t0}

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form, with ranges attached."""
        return {
            "f0_hz": self.f0_hz,
            "v_mps": self.v_mps,
            "d_m": self.d_m,
            "t0_s": self.t0_s,
            "sigma": {k: (None if not np.isfinite(v) else v) for k, v in self.sigma.items()},
            "rms_hz": self.rms_hz,
            "n_points": self.n_points,
            "ranges": self.ranges(),
        }


def preview_curve(
    t_s: ArrayLike, f0: float, v: float, d: float, t0: float, c: float = SPEED_OF_SOUND
) -> FloatArray:
    """Received fundamental frequency at reception times for a straight flyover."""
    return straight_line_doppler(np.asarray(t_s, dtype=float), f0, v, d, t0, c).f


def initial_guess(t: FloatArray, f: FloatArray, c: float = SPEED_OF_SOUND) -> FloatArray:
    """A starting point from the trace itself: asymptote ratio for v, steepest fall for t0 and d.

    The approach and recede asymptotes are f0 (c + v) / c and f0 c / (c + v); their ratio gives
    v, the crossing of their geometric mean gives t0 and f0, and the slope at the crossing,
    -f0 v^2 / (c d), gives d.
    """
    order = np.argsort(t)
    t, f = t[order], f[order]
    f_hi = float(np.percentile(f, 90))
    f_lo = float(np.percentile(f, 10))
    ratio = np.clip(f_hi / max(f_lo, 1e-6), 1.02, 1.8)
    v = c * (ratio - 1.0) / (ratio + 1.0)
    f0 = float(np.sqrt(f_hi * f_lo))
    # the crossing of f0: first point below it after the last point above it
    below = np.flatnonzero(f <= f0)
    t0 = float(t[below[0]]) if below.size else float(np.median(t))
    window = (t >= t0 - 10.0) & (t <= t0 + 10.0)
    if window.sum() >= 3:
        slope = float(np.polyfit(t[window], f[window], 1)[0])
    else:
        slope = (f[-1] - f[0]) / max(float(t[-1] - t[0]), 1e-3)
    d = f0 * v * v / (c * max(-slope, 1e-3))
    return np.array([f0, np.clip(v, 15.0, 0.6 * c), np.clip(d, 50.0, 10000.0), t0])


def fit_straight_flyover(
    t_s: ArrayLike,
    f_hz: ArrayLike,
    c: float = SPEED_OF_SOUND,
    initial: ArrayLike | None = None,
) -> DopplerFit:
    """Fit (f0, v, d, t0) to traced (time, frequency) points by bounded least squares."""
    t = np.asarray(t_s, dtype=float).ravel()
    f = np.asarray(f_hz, dtype=float).ravel()
    ok = np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    if t.size < MIN_POINTS:
        raise ValueError(f"a fit needs at least {MIN_POINTS} points, got {t.size}")
    x0 = np.asarray(initial, dtype=float) if initial is not None else initial_guess(t, f, c)
    lower = np.array([10.0, 5.0, 10.0, float(t.min()) - 600.0])
    upper = np.array([2000.0, 0.9 * c, 50000.0, float(t.max()) + 600.0])
    x0 = np.clip(x0, lower + 1e-6, upper - 1e-6)

    def residuals(x: FloatArray) -> FloatArray:
        return straight_line_doppler(t, x[0], x[1], x[2], x[3], c).f - f

    # soft-L1 loss: a few stray trace points (a noise peak, a harmonic hop) do not drag the fit
    res = least_squares(
        residuals,
        x0,
        bounds=(lower, upper),
        x_scale=[10.0, 10.0, 100.0, 1.0],
        loss="soft_l1",
        f_scale=2.0,
        max_nfev=400,
    )
    r = res.fun
    rms = float(np.sqrt(np.mean(r * r)))
    dof = max(t.size - 4, 1)
    sigma: dict[str, float] = {}
    try:
        cov = np.linalg.inv(res.jac.T @ res.jac) * (float(r @ r) / dof)
        diag = np.diag(cov)
        for name, var in zip(PARAMS, diag, strict=True):
            sigma[name] = float(np.sqrt(var)) if var >= 0.0 and np.isfinite(var) else float("inf")
    except np.linalg.LinAlgError:
        sigma = dict.fromkeys(PARAMS, float("inf"))
    return DopplerFit(
        f0_hz=float(res.x[0]),
        v_mps=float(res.x[1]),
        d_m=float(res.x[2]),
        t0_s=float(res.x[3]),
        sigma=sigma,
        rms_hz=rms,
        n_points=int(t.size),
    )
