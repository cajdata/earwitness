"""The straight-line Doppler fit recovers known flyover parameters from traced points."""

import numpy as np
import pytest

from earwitness.aircraft.fit import (
    DopplerFit,
    fit_straight_flyover,
    initial_guess,
    preview_curve,
)
from earwitness.core.physics import SPEED_OF_SOUND, straight_line_doppler

F0, V, D, T0 = 110.0, 70.0, 400.0, 300.0


def _trace(noise_hz=0.0, seed=3, t_lo=240.0, t_hi=360.0, step=0.25):
    rng = np.random.default_rng(seed)
    t = np.arange(t_lo, t_hi, step)
    f = straight_line_doppler(t, F0, V, D, T0).f + rng.normal(0.0, noise_hz, t.size)
    return t, f


def test_fit_recovers_a_noisy_synthetic_flyover():
    t, f = _trace(noise_hz=0.3)
    fit = fit_straight_flyover(t, f)
    assert abs(fit.f0_hz - F0) < 0.5
    assert abs(fit.v_mps - V) < 3.0
    assert abs(fit.d_m - D) < 40.0
    assert abs(fit.t0_s - T0) < 0.3
    assert fit.rms_hz < 0.5
    assert fit.n_points == t.size
    ranges = fit.ranges()
    for name, truth in (("f0_hz", F0), ("v_mps", V), ("d_m", D), ("t0_s", T0)):
        lo, hi = ranges[name]
        assert lo <= truth <= hi, name
    as_dict = fit.to_dict()
    assert as_dict["ranges"]["v_mps"][0] >= 5.0
    assert all(as_dict["sigma"][k] is not None for k in ("f0_hz", "v_mps", "d_m", "t0_s"))


def test_initial_guess_lands_near_the_truth():
    t, f = _trace()
    f0, v, d, t0 = initial_guess(t, f)
    assert abs(f0 - F0) / F0 < 0.05
    assert abs(v - V) / V < 0.3
    assert abs(t0 - T0) < 5.0
    assert 50.0 <= d <= 10000.0


def test_fit_needs_five_points():
    with pytest.raises(ValueError, match="at least 5"):
        fit_straight_flyover([1.0, 2.0, 3.0], [100.0, 99.0, 98.0])


def test_preview_curve_crosses_f0_one_acoustic_delay_after_t0():
    t = np.array([T0 + D / SPEED_OF_SOUND])
    assert abs(preview_curve(t, F0, V, D, T0)[0] - F0) < 1e-6
    early, late = preview_curve([T0 - 60.0, T0 + 60.0], F0, V, D, T0)
    assert early > F0 > late


def test_unresolved_parameters_get_wide_ranges():
    fit = DopplerFit(
        f0_hz=100.0,
        v_mps=60.0,
        d_m=500.0,
        t0_s=100.0,
        sigma={"f0_hz": 0.1, "v_mps": float("inf"), "d_m": float("inf"), "t0_s": 0.1},
        rms_hz=0.2,
        n_points=20,
    )
    ranges = fit.ranges()
    assert ranges["v_mps"][0] <= 30.0
    assert ranges["v_mps"][1] >= 120.0
    assert ranges["d_m"][0] <= 60.0
    assert ranges["d_m"][1] >= 900.0
    # resolved parameters still get at least their floors
    assert ranges["f0_hz"] == [97.0, 103.0]
    assert ranges["t0_s"] == [97.0, 103.0]


def test_ranges_are_at_least_as_wide_as_the_acceptance_priors():
    t, f = _trace(noise_hz=0.1)
    ranges = fit_straight_flyover(t, f).ranges()
    assert ranges["v_mps"][1] - ranges["v_mps"][0] >= 0.6 * V - 1e-9
    assert ranges["d_m"][1] - ranges["d_m"][0] >= D - 1e-9
    assert ranges["t0_s"][1] - ranges["t0_s"][0] >= 6.0 - 1e-9
    assert ranges["f0_hz"][1] - ranges["f0_hz"][0] >= 6.0 - 1e-9
