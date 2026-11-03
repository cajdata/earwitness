"""Worked numbers for the WGS84 geodesy helpers."""

import numpy as np

from earwitness.core import geodesy as g


def test_ecef_at_equator_and_pole():
    np.testing.assert_allclose(g.geodetic_to_ecef(0.0, 0.0, 0.0), [g.WGS84_A, 0.0, 0.0], atol=1e-6)
    np.testing.assert_allclose(g.geodetic_to_ecef(90.0, 0.0, 0.0), [0.0, 0.0, g.WGS84_B], atol=1e-6)
    assert abs(g.WGS84_B - 6356752.314245) < 1e-6


def test_ecef_roundtrip_many_points():
    lat = np.array([0.0, 45.0, -33.9, 89.9, -89.9, 60.0, 12.3])
    lon = np.array([0.0, 10.0, 151.2, -170.0, 45.0, -97.0, 179.9])
    h = np.array([0.0, 100.0, -50.0, 3000.0, 10.0, 450.0, 12000.0])
    lat2, lon2, h2 = g.ecef_to_geodetic(g.geodetic_to_ecef(lat, lon, h))
    np.testing.assert_allclose(lat2, lat, atol=1e-9)
    np.testing.assert_allclose(lon2, lon, atol=1e-9)
    np.testing.assert_allclose(h2, h, atol=1e-6)


def test_enu_matches_known_arc_lengths_at_the_equator():
    # 0.001 degree of longitude at the equator is 111.3195 m (a * pi / 180 / 1000).
    e = g.geodetic_to_enu(0.0, 0.001, 0.0, 0.0, 0.0, 0.0)
    assert abs(e[0] - 111.3195) < 1e-3
    assert abs(e[1]) < 1e-6
    assert -0.0011 < e[2] < -0.0009  # the tangent plane leaves the ellipsoid: -E^2 / (2a)
    # 0.001 degree of latitude at the equator is 110.5744 m (meridian radius a (1 - e^2)).
    n = g.geodetic_to_enu(0.001, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert abs(n[1] - 110.5744) < 1e-3
    assert abs(n[0]) < 1e-6
    assert -0.0011 < n[2] < -0.0009


def test_slant_range_horizontal_distance_and_bearing():
    lat0, lon0 = 35.0, -97.0
    lat, lon = g.destination(lat0, lon0, 90.0, 10000.0)
    assert abs(float(g.horizontal_distance(lat, lon, lat0, lon0)) - 10000.0) < 1e-6
    assert abs(float(g.bearing(lat, lon, lat0, lon0)) - 90.0) < 1e-6
    slant = float(g.slant_range(lat, lon, 2000.0, lat0, lon0, 0.0))
    flat = float(np.hypot(10000.0, 2000.0))  # 10198.04 m, the handoff's thunder formula
    assert slant > 10000.0
    assert abs(slant - flat) < 3.0  # curvature lowers a 10 km distant source by ~8 m


def test_destination_inverts_distance_and_bearing():
    lat0, lon0 = 60.5, 5.3
    for b, d in [
        (0.0, 1000.0),
        (37.0, 15000.0),
        (123.4, 25000.0),
        (270.0, 500.0),
        (359.0, 20000.0),
    ]:
        lat, lon = g.destination(lat0, lon0, b, d)
        assert abs(float(g.horizontal_distance(lat, lon, lat0, lon0)) - d) < 1e-6
        got = float(g.bearing(lat, lon, lat0, lon0))
        assert abs((got - b + 180.0) % 360.0 - 180.0) < 1e-6


def test_destination_broadcasts():
    lat, lon = g.destination(35.0, -97.0, np.array([0.0, 90.0, 180.0]), 5000.0)
    assert lat.shape == (3,)
    assert lat[0] > 35.0 > lat[2]
    assert lon[1] > -97.0


def test_haversine_agrees_with_tangent_plane_at_25_km():
    lat0, lon0 = 35.0, -97.0
    lat, lon = g.destination(lat0, lon0, 45.0, 25000.0)
    hav = float(g.haversine(lat0, lon0, lat, lon))
    assert abs(hav - 25000.0) / 25000.0 < 0.003  # sphere vs ellipsoid


def test_enu_ecef_roundtrip():
    rng = np.random.default_rng(0)
    enu = rng.uniform(-30000.0, 30000.0, size=(50, 3))
    back = g.ecef_to_enu(g.enu_to_ecef(enu, 35.0, -97.0, 300.0), 35.0, -97.0, 300.0)
    np.testing.assert_allclose(back, enu, atol=1e-6)


def test_vectorized_shapes():
    lat = np.zeros((4, 5))
    lon = np.linspace(0.0, 1.0, 20).reshape(4, 5)
    assert g.geodetic_to_ecef(lat, lon, 0.0).shape == (4, 5, 3)
    assert g.horizontal_distance(lat, lon, 0.0, 0.0).shape == (4, 5)
    assert g.slant_range(lat, lon, 100.0, 0.0, 0.0).shape == (4, 5)
