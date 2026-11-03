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
