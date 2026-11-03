"""WGS84 geodesy: geodetic, ECEF and local ENU frames, slant ranges and bearings.

Angles are degrees at the API boundary; everything else is meters. Functions accept scalars or
numpy arrays and broadcast like numpy. The reference point of an ENU frame is always a scalar
(lat, lon, height). ENU frames are tangent planes, so "horizontal distance" is the straight-line
distance in that plane; it differs from the geodesic by centimeters at 25 km, which is far below
the kilometer-level location error of free lightning data.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]

WGS84_A = 6378137.0  # semi-major axis, m
WGS84_F = 1.0 / 298.257223563  # flattening
WGS84_B = WGS84_A * (1.0 - WGS84_F)  # semi-minor axis, m
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)  # first eccentricity squared
EARTH_MEAN_RADIUS = 6371008.8  # IUGG mean radius, m; only the spherical helper uses it


def geodetic_to_ecef(lat_deg: ArrayLike, lon_deg: ArrayLike, h_m: ArrayLike = 0.0) -> FloatArray:
    """Geodetic (deg, deg, m above the ellipsoid) to ECEF meters, shape (..., 3)."""
    lat = np.deg2rad(np.asarray(lat_deg, dtype=float))
    lon = np.deg2rad(np.asarray(lon_deg, dtype=float))
    h = np.asarray(h_m, dtype=float)
    sin_lat, cos_lat = np.sin(lat), np.cos(lat)
    n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
    x = (n + h) * cos_lat * np.cos(lon)
    y = (n + h) * cos_lat * np.sin(lon)
    z = (n * (1.0 - WGS84_E2) + h) * sin_lat
    return np.stack(np.broadcast_arrays(x, y, z), axis=-1)


def ecef_to_geodetic(xyz: ArrayLike) -> tuple[FloatArray, FloatArray, FloatArray]:
    """ECEF meters, shape (..., 3), to geodetic (lat deg, lon deg, h m).

    Fixed-point iteration on the latitude; each pass gains a factor of about e^2, so eight
    passes are converged to double precision everywhere, poles included.
    """
    p = np.asarray(xyz, dtype=float)
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    lon = np.arctan2(y, x)
    rho = np.hypot(x, y)
    lat = np.arctan2(z, rho * (1.0 - WGS84_E2))
    for _ in range(8):
        sin_lat = np.sin(lat)
        n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
        lat = np.arctan2(z + WGS84_E2 * n * sin_lat, rho)
    sin_lat, cos_lat = np.sin(lat), np.cos(lat)
    h = rho * cos_lat + z * sin_lat - WGS84_A * np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
    return np.rad2deg(lat), np.rad2deg(lon), h


def _enu_rotation(lat0_deg: float, lon0_deg: float) -> FloatArray:
    """Rows are the east, north and up unit vectors of the tangent frame, in ECEF."""
    lat0 = np.deg2rad(lat0_deg)
    lon0 = np.deg2rad(lon0_deg)
    sl, cl = np.sin(lat0), np.cos(lat0)
    so, co = np.sin(lon0), np.cos(lon0)
    return np.array(
        [
            [-so, co, 0.0],
            [-sl * co, -sl * so, cl],
            [cl * co, cl * so, sl],
        ]
    )


def ecef_to_enu(xyz: ArrayLike, lat0_deg: float, lon0_deg: float, h0_m: float = 0.0) -> FloatArray:
    """ECEF meters to local east-north-up meters about the reference point."""
    d = np.asarray(xyz, dtype=float) - geodetic_to_ecef(lat0_deg, lon0_deg, h0_m)
    return d @ _enu_rotation(lat0_deg, lon0_deg).T


def enu_to_ecef(enu: ArrayLike, lat0_deg: float, lon0_deg: float, h0_m: float = 0.0) -> FloatArray:
    """Local east-north-up meters about the reference point to ECEF meters."""
    rot = _enu_rotation(lat0_deg, lon0_deg)
    return np.asarray(enu, dtype=float) @ rot + geodetic_to_ecef(lat0_deg, lon0_deg, h0_m)


def geodetic_to_enu(
    lat_deg: ArrayLike,
    lon_deg: ArrayLike,
    h_m: ArrayLike,
    lat0_deg: float,
    lon0_deg: float,
    h0_m: float = 0.0,
) -> FloatArray:
    """Geodetic points to ENU meters about the reference point, shape (..., 3)."""
    return ecef_to_enu(geodetic_to_ecef(lat_deg, lon_deg, h_m), lat0_deg, lon0_deg, h0_m)


def enu_to_geodetic(
    enu: ArrayLike, lat0_deg: float, lon0_deg: float, h0_m: float = 0.0
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """ENU meters about the reference point to geodetic (lat deg, lon deg, h m)."""
    return ecef_to_geodetic(enu_to_ecef(enu, lat0_deg, lon0_deg, h0_m))


def slant_range(
    lat_deg: ArrayLike,
    lon_deg: ArrayLike,
    h_m: ArrayLike,
    lat0_deg: float,
    lon0_deg: float,
    h0_m: float = 0.0,
) -> FloatArray:
    """Straight-line distance from the reference point to each geodetic point, m."""
    return np.linalg.norm(geodetic_to_enu(lat_deg, lon_deg, h_m, lat0_deg, lon0_deg, h0_m), axis=-1)


def horizontal_distance(
    lat_deg: ArrayLike, lon_deg: ArrayLike, lat0_deg: float, lon0_deg: float
) -> FloatArray:
    """Horizontal (tangent-plane) distance from the reference point, m, heights ignored."""
    enu = geodetic_to_enu(lat_deg, lon_deg, 0.0, lat0_deg, lon0_deg, 0.0)
    return np.hypot(enu[..., 0], enu[..., 1])


def bearing(lat_deg: ArrayLike, lon_deg: ArrayLike, lat0_deg: float, lon0_deg: float) -> FloatArray:
    """Bearing from the reference point to each point, degrees clockwise from north in [0, 360)."""
    enu = geodetic_to_enu(lat_deg, lon_deg, 0.0, lat0_deg, lon0_deg, 0.0)
    return np.mod(np.rad2deg(np.arctan2(enu[..., 0], enu[..., 1])), 360.0)


def destination(
    lat0_deg: float, lon0_deg: float, bearing_deg: ArrayLike, distance_m: ArrayLike
) -> tuple[FloatArray, FloatArray]:
    """Point on the ellipsoid whose tangent-plane offset from the reference point is
    (d sin b, d cos b) east and north. Exact inverse of `horizontal_distance` and `bearing`.

    The point is base + u * up with base in the tangent plane; u solves the quadratic that puts
    the point on the ellipsoid, taking the root near zero in its cancellation-free form.
    """
    b = np.deg2rad(np.asarray(bearing_deg, dtype=float))
    d = np.asarray(distance_m, dtype=float)
    e, n = np.broadcast_arrays(d * np.sin(b), d * np.cos(b))
    rot = _enu_rotation(lat0_deg, lon0_deg)
    origin = geodetic_to_ecef(lat0_deg, lon0_deg, 0.0)
    base = origin + e[..., None] * rot[0] + n[..., None] * rot[1]
    up = rot[2]
    scale = np.array([1.0 / WGS84_A, 1.0 / WGS84_A, 1.0 / WGS84_B])
    bs = base * scale
    us = up * scale
    qa = float(np.dot(us, us))
    qb = 2.0 * np.sum(bs * us, axis=-1)
    qc = np.sum(bs * bs, axis=-1) - 1.0
    u = -2.0 * qc / (qb + np.sqrt(qb * qb - 4.0 * qa * qc))
    lat, lon, _ = ecef_to_geodetic(base + u[..., None] * up)
    return lat, lon


def haversine(
    lat1_deg: ArrayLike, lon1_deg: ArrayLike, lat2_deg: ArrayLike, lon2_deg: ArrayLike
) -> FloatArray:
    """Great-circle distance on a sphere of mean radius, m. For sanity checks and coarse grids."""
    lat1 = np.deg2rad(np.asarray(lat1_deg, dtype=float))
    lat2 = np.deg2rad(np.asarray(lat2_deg, dtype=float))
    dlat = lat2 - lat1
    dlon = np.deg2rad(np.asarray(lon2_deg, dtype=float) - np.asarray(lon1_deg, dtype=float))
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2.0 * EARTH_MEAN_RADIUS * np.arcsin(np.sqrt(a))
