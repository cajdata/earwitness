"""Worked numbers for sound speed, Doppler shift and the emission-time solver.

The straight flyover used throughout: 100 m/s along +x, 300 m overhead, closest approach at
t = 0, rest frequency 130 Hz, c = 343 m/s (the handoff's video-3 numbers).
"""

import numpy as np

from earwitness.core import physics as ph

V, D, T0, F0, C = 100.0, 300.0, 0.0, 130.0, 343.0
ORIGIN = np.zeros(3)


def _position(t):
    t = np.asarray(t, dtype=float)
    return np.stack([V * (t - T0), np.zeros_like(t), np.full_like(t, D)], axis=-1)


def _velocity(t):
    t = np.asarray(t, dtype=float)
    return np.stack([np.full_like(t, V), np.zeros_like(t), np.zeros_like(t)], axis=-1)


def test_speed_of_sound_worked_numbers():
    assert abs(ph.speed_of_sound(20.0) - 343.2) < 0.3
    assert abs(ph.speed_of_sound(0.0) - 331.3) < 1e-9
    assert ph.speed_of_sound(30.0) > ph.speed_of_sound(10.0)
    assert ph.SPEED_OF_SOUND == 343.0


def test_doppler_worked_example_from_the_video():
    f = float(ph.doppler_frequency(130.0, -57.97))
    assert round(f, 2) == 156.44
    assert abs(f - 156.44) < 0.005


def test_doppler_inverse_and_sign():
    f = ph.doppler_frequency(130.0, -57.97)
    assert abs(float(ph.radial_velocity_from_frequency(f, 130.0)) + 57.97) < 1e-9
    assert float(ph.doppler_frequency(130.0, 57.97)) < 130.0  # receding is heard lower


def test_radial_velocity_sign_convention():
    # A source 1 km east moving west at 50 m/s approaches: v_r = -50.
    assert abs(float(ph.radial_velocity([1000.0, 0, 0], [-50.0, 0, 0], ORIGIN)) + 50.0) < 1e-12
    # Tangential motion gives zero radial velocity.
    assert abs(float(ph.radial_velocity([1000.0, 0, 0], [0, 50.0, 0], ORIGIN))) < 1e-12


def test_straight_line_closed_form_satisfies_the_reception_equation():
    t_r = np.linspace(-60.0, 60.0, 1201)
    c = ph.straight_line_doppler(t_r, F0, V, D, T0, C)
    np.testing.assert_allclose(c.t_e + c.r / C, t_r, atol=1e-9)
    assert np.all(c.t_e < t_r)
    assert np.all(np.diff(c.f) < 0.0)  # monotone from the high approach to the low recession
    assert abs(c.f[0] - F0 * C / (C - V)) < 0.3  # 183.5 Hz asymptote
    assert abs(c.f[-1] - F0 * C / (C + V)) < 0.3  # 100.7 Hz asymptote
    # f0 is heard when the closest-approach emission arrives, d / c after t0.
    i = int(np.argmin(np.abs(c.f - F0)))
    assert abs(t_r[i] - (T0 + D / C)) < 0.1


def test_fixed_point_solver_converges_within_five_iterations():
    t_r = np.linspace(-60.0, 60.0, 2401)
    exact = ph.straight_line_emission_time(t_r, V, D, T0, C)
    delay = t_r - exact
    t_e5 = ph.solve_emission_time(_position, ORIGIN, t_r, C, iterations=5)
    relative = np.abs(t_e5 - exact) / delay
    assert relative.max() < 0.01, f"max residual after 5 passes: {relative.max():.4%} of the delay"
    f5 = ph.doppler_curve(_position, _velocity, ORIGIN, t_r, F0, C, iterations=5).f
    f_exact = ph.straight_line_doppler(t_r, F0, V, D, T0, C).f
    assert np.max(np.abs(f5 - f_exact)) < 0.01, "5 passes must be exact to 0.01 Hz"
    t_e30 = ph.solve_emission_time(_position, ORIGIN, t_r, C, iterations=30)
    assert np.max(np.abs(t_e30 - exact)) < 1e-9


def test_reception_time_model_has_a_different_shape_not_just_a_delay():
    t_r = np.linspace(-30.0, 30.0, 6001)
    right = ph.doppler_curve(_position, _velocity, ORIGIN, t_r, F0, C, iterations=30)
    wrong = ph.doppler_curve(_position, _velocity, ORIGIN, t_r, F0, C, use_emission_time=False)

    # 1. The wrong model is exactly antisymmetric in radial velocity about its f0 crossing.
    #    The right one is not: approach is compressed and recession stretched in reception time.
    assert np.max(np.abs(wrong.v_r + wrong.v_r[::-1])) < 1e-9
    t_cross = T0 + D / C
    delta = np.linspace(0.5, 20.0, 400)
    plus = ph.doppler_curve(_position, _velocity, ORIGIN, t_cross + delta, F0, C, 30).v_r
    minus = ph.doppler_curve(_position, _velocity, ORIGIN, t_cross - delta, F0, C, 30).v_r
    asymmetry = np.max(np.abs(plus + minus))
    assert asymmetry > 3.0, f"right model asymmetry only {asymmetry:.2f} m/s"

    # 2. No pure time shift of the wrong curve reproduces the right one.
    core = np.abs(t_r) < 25.0
    best = np.inf
    for shift in np.arange(0.0, 3.0, 0.005):
        shifted = np.interp(t_r, t_r + shift, wrong.f)
        best = min(best, float(np.max(np.abs(right.f[core] - shifted[core]))))
    assert best > 1.0, f"best residual after time alignment: {best:.2f} Hz"
