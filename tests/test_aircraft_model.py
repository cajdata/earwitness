"""Tracks and the exact Doppler curve against the closed-form straight-line solution."""

import numpy as np
import pytest

from earwitness.aircraft import model as am
from earwitness.core import physics as ph

ORIGIN = np.zeros(3)


def test_straight_track_geometry_and_closest_approach():
    tr = am.straight_track(100.0, 90.0, 300.0, 400.0, t_cpa_s=50.0, t_start=0.0, t_end=100.0)
    # Heading 90 is due east; the offset is to the right of the heading, so south.
    np.testing.assert_allclose(tr.velocity(50.0), [100.0, 0.0, 0.0], atol=1e-9)
    np.testing.assert_allclose(tr.position(50.0), [0.0, -400.0, 300.0], atol=1e-9)
    t_cpa, slant = am.closest_approach(tr)
    assert abs(t_cpa - 50.0) < 1e-4
    assert abs(slant - 500.0) < 1e-6
    assert abs(am.heard_cpa_time(tr, ORIGIN) - (50.0 + 500.0 / 343.0)) < 1e-4
    assert tr.span == (0.0, 100.0)
    assert abs(float(tr.speed(12.3)) - 100.0) < 1e-9


def test_exact_curve_matches_closed_form_on_a_straight_track():
    tr = am.straight_track(100.0, 37.0, 300.0, 0.0, 60.0, 0.0, 120.0)
    t_r = np.linspace(5.0, 115.0, 1101)
    curve = am.doppler_curve(tr, ORIGIN, t_r, 130.0, iterations=40)
    ref = ph.straight_line_doppler(t_r, 130.0, 100.0, 300.0, 60.0)
    np.testing.assert_allclose(curve.f, ref.f, atol=1e-6)
    np.testing.assert_allclose(curve.t_e, ref.t_e, atol=1e-7)
    np.testing.assert_allclose(curve.r, ref.r, atol=1e-4)


def test_curve_is_defined_slightly_before_the_first_sample():
    # Emission times fall before reception times; the spline extrapolates linearly.
    tr = am.straight_track(100.0, 0.0, 300.0, 0.0, 60.0, 50.0, 70.0)
    curve = am.doppler_curve(tr, ORIGIN, np.array([50.0]), 130.0)
    assert curve.t_e[0] < 50.0
    assert np.isfinite(curve.f[0])


def test_geodetic_roundtrip_and_resample():
    tr = am.straight_track(
        70.0, 123.0, 450.0, 300.0, 100.0, 0.0, 200.0, step_s=2.0, icao="abc123", type_code="C172"
    )
    lat, lon, h = tr.to_geodetic(35.0, -97.0, 300.0)
    back = am.Track.from_geodetic(tr.t, lat, lon, h, 35.0, -97.0, 300.0, icao="abc123")
    np.testing.assert_allclose(back.enu, tr.enu, atol=1e-6)
    assert h.min() > 300.0 + 450.0 - 1e-6  # ellipsoidal height grows away from the observer
    fine = tr.resample(1.0)
    assert fine.t.size == 201
    np.testing.assert_allclose(fine.position(57.5), tr.position(57.5), atol=1e-9)
    assert fine.icao == "abc123"
    assert fine.type_code == "C172"


def test_track_validation():
    with pytest.raises(ValueError, match="strictly increasing"):
        am.Track([0.0, 1.0, 1.0], np.zeros((3, 3)))
    with pytest.raises(ValueError, match="shape"):
        am.Track([0.0, 1.0], np.zeros((3, 3)))
    with pytest.raises(ValueError, match="at least two"):
        am.Track([0.0], np.zeros((1, 3)))
    with pytest.raises(ValueError, match="t_end"):
        am.straight_track(100.0, 0.0, 300.0, 0.0, 0.0, 10.0, 10.0)


def test_track_continues_straight_beyond_its_span():
    # a tight circle: a cubic extrapolated 100 s past the last sample would be tens of km off,
    # while the straight-line continuation moves at the end speed in the end direction
    t = np.arange(0.0, 63.0)
    omega = 50.0 / 500.0
    enu = np.column_stack(
        [500.0 * np.cos(omega * t), 500.0 * np.sin(omega * t), np.full(t.size, 300.0)]
    )
    tr = am.Track(t, enu)
    t1 = tr.span[1]
    v_end = tr.velocity(t1)
    np.testing.assert_allclose(tr.position(t1 + 100.0), tr.position(t1) + 100.0 * v_end, atol=1e-6)
    np.testing.assert_allclose(tr.velocity(t1 + 100.0), v_end, atol=1e-9)
    assert abs(float(tr.speed(t1 + 100.0)) - 50.0) < 0.5
    v_start = tr.velocity(0.0)
    np.testing.assert_allclose(tr.position(-30.0), tr.position(0.0) - 30.0 * v_start, atol=1e-6)
    # inside the span nothing changed
    np.testing.assert_allclose(tr.position(31.0), enu[31], atol=1e-9)


def _two_pass_track():
    """A 40 m/s pass 671 m from the origin closest at 300 s, then a 90 m/s pass back across
    it, 335 m off (150 m beside its line, 300 m up), closest at about 733 s."""
    ta = np.arange(0.0, 600.0, 1.0)
    a = np.column_stack([40.0 * (ta - 300.0), np.full(ta.size, 600.0), np.full(ta.size, 300.0)])
    start = a[-1] + np.array([40.0, 0.0, 0.0])
    direction = np.array([-12000.0, -300.0, 300.0]) - start
    direction /= np.linalg.norm(direction)
    tb = np.arange(600.0, 860.0, 1.0)
    b = start[None, :] + 90.0 * (tb - 600.0)[:, None] * direction[None, :]
    return am.Track(np.r_[ta, tb], np.vstack([a, b]))


def test_closest_approach_within_a_heard_window_picks_the_pass_heard():
    tr = _two_pass_track()
    t_cpa, slant = am.closest_approach(tr)
    assert 700.0 < t_cpa < 760.0  # the nearer, later pass
    assert abs(slant - 335.4) < 1.0
    # sound from the first pass's closest approach arrives at 300 + 670.8 / 343 = 301.96 s
    t_cpa, slant = am.closest_approach(tr, heard_window=(290.0, 320.0))
    assert abs(t_cpa - 300.0) < 1e-3
    assert abs(slant - float(np.hypot(600.0, 300.0))) < 1e-3
    # a window no sample's sound reaches falls back to the whole track
    t_cpa, _ = am.closest_approach(tr, heard_window=(5000.0, 5100.0))
    assert t_cpa > 700.0
