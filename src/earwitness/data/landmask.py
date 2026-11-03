"""Land mask from Natural Earth land polygons (public domain), cached as GeoJSON."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import shapely
from numpy.typing import ArrayLike, NDArray
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from earwitness.data.cache import cache_root, download

NATURAL_EARTH_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
    "ne_{scale}_land.geojson"
)
SCALES = ("10m", "50m", "110m")


class LandMask:
    """Point-in-land test over a set of polygons, backed by an STR tree."""

    def __init__(self, polygons: Sequence[BaseGeometry]) -> None:
        geoms: list[BaseGeometry] = []
        for g in polygons:
            if g.geom_type == "MultiPolygon":
                geoms.extend(g.geoms)
            else:
                geoms.append(g)
        self._geoms = np.array(geoms, dtype=object)
        self._tree = STRtree(self._geoms)

    def __len__(self) -> int:
        return int(self._geoms.size)

    @classmethod
    def from_geojson(cls, path: str | Path) -> LandMask:
        """Load a GeoJSON FeatureCollection (or a single geometry) of land polygons."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("type") == "FeatureCollection":
            return cls([shape(f["geometry"]) for f in data["features"]])
        if data.get("type") == "Feature":
            return cls([shape(data["geometry"])])
        return cls([shape(data)])

    @classmethod
    def natural_earth(cls, root: Path | None = None, scale: str = "50m") -> LandMask:
        """Natural Earth land at 1:10m, 1:50m or 1:110m, downloaded once into the cache."""
        if scale not in SCALES:
            raise ValueError(f"scale must be one of {SCALES}")
        path = cache_root(root) / "landmask" / f"ne_{scale}_land.geojson"
        if not path.exists():
            download(NATURAL_EARTH_URL.format(scale=scale), path)
        return cls.from_geojson(path)

    def contains(self, lat: ArrayLike, lon: ArrayLike) -> NDArray[np.bool_]:
        """True where a point lies on land."""
        lat_arr = np.atleast_1d(np.asarray(lat, dtype=float))
        lon_arr = np.atleast_1d(np.asarray(lon, dtype=float))
        points = shapely.points(lon_arr, lat_arr)
        hits = self._tree.query(points, predicate="within")
        mask = np.zeros(lat_arr.size, dtype=bool)
        mask[hits[0]] = True
        return mask
