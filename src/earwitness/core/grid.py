"""Candidate grids: regular grids at a metric spacing over a bounding box, and local grids."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import shapely
from numpy.typing import ArrayLike, NDArray
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from earwitness.core import geodesy

FloatArray = NDArray[np.float64]
METERS_PER_DEGREE = geodesy.EARTH_MEAN_RADIUS * np.pi / 180.0
"""Mean-sphere meters per degree; used for box sizing, not for grid spacing."""


def ellipsoid_radii(lat_deg: float) -> tuple[float, float]:
    """Meridional (north-south) and prime-vertical (east-west) radii of curvature, m."""
    s = np.sin(np.deg2rad(lat_deg))
    den = np.sqrt(1.0 - geodesy.WGS84_E2 * s * s)
    n = geodesy.WGS84_A / den
    m = geodesy.WGS84_A * (1.0 - geodesy.WGS84_E2) / den**3
    return float(m), float(n)


@dataclass(frozen=True)
class BBox:
    """Geographic bounding box in degrees: west, south, east, north."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        if not (-180.0 <= self.west < self.east <= 180.0):
            raise ValueError(f"bad longitude range {self.west}..{self.east}")
        if not (-90.0 <= self.south < self.north <= 90.0):
            raise ValueError(f"bad latitude range {self.south}..{self.north}")

    @classmethod
    def parse(cls, text: str) -> BBox:
        """Parse 'west,south,east,north' (commas or spaces between the numbers)."""
        parts = [float(p) for p in text.replace(",", " ").split()]
        if len(parts) != 4:
            raise ValueError("bbox must be west,south,east,north")
        return cls(*parts)

    @classmethod
    def around(cls, lat: float, lon: float, half_extent_m: float) -> BBox:
        """Box of half-width `half_extent_m` around a point."""
        dlat = half_extent_m / METERS_PER_DEGREE
        dlon = half_extent_m / (METERS_PER_DEGREE * max(np.cos(np.deg2rad(lat)), 0.05))
        return cls(
            max(lon - dlon, -180.0),
            max(lat - dlat, -90.0),
            min(lon + dlon, 180.0),
            min(lat + dlat, 90.0),
        )

    def expand(self, margin_m: float) -> BBox:
        """Grow (or with a negative margin, shrink) the box by a distance on every side."""
        dlat = margin_m / METERS_PER_DEGREE
        widest = max(abs(self.south), abs(self.north))
        dlon = margin_m / (METERS_PER_DEGREE * max(np.cos(np.deg2rad(widest)), 0.05))
        return BBox(
            max(self.west - dlon, -180.0),
            max(self.south - dlat, -90.0),
            min(self.east + dlon, 180.0),
            min(self.north + dlat, 90.0),
        )

    def contains(self, lat: ArrayLike, lon: ArrayLike) -> NDArray[np.bool_]:
        """Mask of points inside the box (inclusive)."""
        la = np.asarray(lat, dtype=float)
        lo = np.asarray(lon, dtype=float)
        return (la >= self.south) & (la <= self.north) & (lo >= self.west) & (lo <= self.east)

    @property
    def center(self) -> tuple[float, float]:
        """(lat, lon) of the box center."""
        return 0.5 * (self.south + self.north), 0.5 * (self.west + self.east)

    def area_km2(self) -> float:
        """Approximate area."""
        height = (self.north - self.south) * METERS_PER_DEGREE
        width = (self.east - self.west) * METERS_PER_DEGREE * np.cos(np.deg2rad(self.center[0]))
        return float(height * width / 1e6)

    def as_tuple(self) -> tuple[float, float, float, float]:
        """(west, south, east, north)."""
        return (self.west, self.south, self.east, self.north)

    def __str__(self) -> str:
        return f"{self.west:g},{self.south:g},{self.east:g},{self.north:g}"


def parse_bbox(tokens: str | Sequence[str]) -> BBox:
    """Parse a bbox given as one 'w,s,e,n' string or as separate command-line tokens."""
    text = tokens if isinstance(tokens, str) else " ".join(tokens)
    return BBox.parse(text)


def grid_points(bbox: BBox, spacing_m: float) -> tuple[FloatArray, FloatArray]:
    """Points on rows `spacing_m` apart in latitude, `spacing_m` apart along each row.

    Spacing uses the WGS84 radii of curvature (meridional at the box center, prime vertical per
    row), so the ground spacing is exact to a few centimeters across a few hundred kilometers.
    """
    if spacing_m <= 0:
        raise ValueError("spacing must be positive")
    meridional, _ = ellipsoid_radii(bbox.center[0])
    dlat = np.rad2deg(spacing_m / meridional)
    lats = np.arange(bbox.south + dlat / 2.0, bbox.north, dlat)
    out_lat, out_lon = [], []
    for lat in lats:
        _, prime = ellipsoid_radii(float(lat))
        dlon = np.rad2deg(spacing_m / (prime * max(np.cos(np.deg2rad(lat)), 1e-6)))
        lons = np.arange(bbox.west + dlon / 2.0, bbox.east, dlon)
        out_lat.append(np.full(lons.size, lat))
        out_lon.append(lons)
    if not out_lat:
        return np.zeros(0), np.zeros(0)
    return np.concatenate(out_lat), np.concatenate(out_lon)


def local_grid(
    lat0: float, lon0: float, half_extent_m: float, spacing_m: float
) -> tuple[FloatArray, FloatArray]:
    """Square tangent-plane grid centered on a point, including the center."""
    n = round(half_extent_m / spacing_m)
    offsets = np.arange(-n, n + 1) * spacing_m
    east, north = np.meshgrid(offsets, offsets)
    enu = np.stack([east.ravel(), north.ravel(), np.zeros(east.size)], axis=-1)
    lat, lon, _ = geodesy.enu_to_geodetic(enu, lat0, lon0, 0.0)
    return lat, lon


def geometry_from_geojson(data: dict[str, object]) -> BaseGeometry:
    """Geometry of a GeoJSON Feature, FeatureCollection (union) or bare geometry."""
    kind = data.get("type")
    if kind == "FeatureCollection":
        feats = data["features"]  # type: ignore[index]
        return shapely.union_all([shape(f["geometry"]) for f in feats])  # type: ignore[index]
    if kind == "Feature":
        return shape(data["geometry"])  # type: ignore[arg-type]
    return shape(data)


def geometry_bbox(geom: BaseGeometry) -> BBox:
    """Bounding box of a geometry."""
    west, south, east, north = geom.bounds
    return BBox(west, south, east, north)


def points_in_geometry(lat: ArrayLike, lon: ArrayLike, geom: BaseGeometry) -> NDArray[np.bool_]:
    """Mask of points inside a polygon geometry."""
    return shapely.contains_xy(geom, np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
