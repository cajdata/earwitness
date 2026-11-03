"""Terrain heights: Copernicus GLO-30 tiles from AWS Open Data, plus the EGM96 geoid grid.

Copernicus DEM heights are orthometric (above the EGM2008 geoid); ADS-B geometric altitude is
height above the WGS84 ellipsoid. Adding the geoid undulation puts the ground on the ellipsoid
too, so aircraft and ground share one vertical datum. The EGM96 grid from the PROJ CDN differs
from EGM2008 by decimeters, far below the errors that matter here.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import numpy as np
import rasterio
from numpy.typing import ArrayLike, NDArray
from rasterio.transform import rowcol

from earwitness.data.cache import cache_root, download

FloatArray = NDArray[np.float64]

COPERNICUS_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{tile}/{tile}.tif"
EGM96_URL = "https://cdn.proj.org/us_nga_egm96_15.tif"


def tile_id(lat: float, lon: float) -> str:
    """Copernicus GLO-30 tile covering a point, e.g. Copernicus_DSM_COG_10_N35_00_W098_00_DEM."""
    la = int(np.floor(lat))
    lo = int(np.floor(lon))
    ns = f"N{la:02d}" if la >= 0 else f"S{-la:02d}"
    ew = f"E{lo:03d}" if lo >= 0 else f"W{-lo:03d}"
    return f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"


def tile_path(root: Path, tile: str) -> Path:
    """Cached GeoTIFF path for a tile."""
    return root / "dem" / "copernicus_glo30" / f"{tile}.tif"


class Dem:
    """Orthometric ground elevation from cached Copernicus GLO-30 tiles; sea tiles read as 0 m."""

    def __init__(
        self, root: Path | None = None, max_open: int = 16, allow_download: bool = True
    ) -> None:
        self.root = cache_root(root)
        self.allow_download = allow_download
        self._max_open = max_open
        self._tiles: OrderedDict[str, tuple[rasterio.io.DatasetReader, np.ndarray] | None] = (
            OrderedDict()
        )
        self.missing: set[str] = set()

    def _open(self, tile: str) -> tuple[rasterio.io.DatasetReader, np.ndarray] | None:
        if tile in self._tiles:
            self._tiles.move_to_end(tile)
            return self._tiles[tile]
        path = tile_path(self.root, tile)
        if not path.exists():
            if tile in self.missing or not self.allow_download:
                return None
            try:
                download(COPERNICUS_URL.format(tile=tile), path, timeout=600.0)
            except RuntimeError:
                self.missing.add(tile)
                return None
        ds = rasterio.open(path)
        band = ds.read(1).astype(np.float32)
        if ds.nodata is not None:
            band[band == ds.nodata] = np.nan
        self._tiles[tile] = (ds, band)
        while len(self._tiles) > self._max_open:
            _, old = self._tiles.popitem(last=False)
            if old is not None:
                old[0].close()
        return self._tiles[tile]

    def elevation(self, lat: ArrayLike, lon: ArrayLike) -> FloatArray:
        """Ground height above the EGM2008 geoid, m, nearest cell; 0 where no tile exists."""
        la = np.atleast_1d(np.asarray(lat, dtype=float)).ravel()
        lo = np.atleast_1d(np.asarray(lon, dtype=float)).ravel()
        out = np.zeros(la.shape, dtype=float)
        keys = np.array([tile_id(a, b) for a, b in zip(la, lo, strict=True)])
        for tile in np.unique(keys):
            idx = np.flatnonzero(keys == tile)
            opened = self._open(str(tile))
            if opened is None:
                continue
            ds, band = opened
            rows, cols = rowcol(ds.transform, lo[idx], la[idx])
            rows = np.clip(np.asarray(rows), 0, band.shape[0] - 1)
            cols = np.clip(np.asarray(cols), 0, band.shape[1] - 1)
            values = band[rows, cols]
            out[idx] = np.where(np.isnan(values), 0.0, values)
        return out

    def close(self) -> None:
        """Release open tiles."""
        for item in self._tiles.values():
            if item is not None:
                item[0].close()
        self._tiles.clear()


class Geoid:
    """EGM96 geoid undulation (geoid height above the WGS84 ellipsoid), bilinear from a grid."""

    def __init__(self, root: Path | None = None, path: Path | None = None) -> None:
        if path is None:
            path = cache_root(root) / "dem" / "egm96_15.tif"
            if not path.exists():
                download(EGM96_URL, path)
        with rasterio.open(path) as ds:
            self._grid = ds.read(1).astype(float)
            t = ds.transform
            self._x0, self._dx = float(t.c), float(t.a)
            self._y0, self._dy = float(t.f), float(t.e)
            if ds.nodata is not None:
                self._grid[self._grid == ds.nodata] = np.nan
        self._lon_max = self._x0 + self._dx * self._grid.shape[1]

    def undulation(self, lat: ArrayLike, lon: ArrayLike) -> FloatArray:
        """Geoid height above the ellipsoid, m (ellipsoidal = orthometric + undulation)."""
        la = np.atleast_1d(np.asarray(lat, dtype=float)).ravel()
        lo = np.atleast_1d(np.asarray(lon, dtype=float)).ravel().copy()
        if self._x0 >= -1e-9:  # grid spans 0..360
            lo = np.where(lo < 0.0, lo + 360.0, lo)
        cols = (lo - self._x0) / self._dx - 0.5
        rows = (la - self._y0) / self._dy - 0.5
        nrow, ncol = self._grid.shape
        r0 = np.clip(np.floor(rows).astype(int), 0, nrow - 2)
        c0 = np.clip(np.floor(cols).astype(int), 0, ncol - 2)
        fr = np.clip(rows - r0, 0.0, 1.0)
        fc = np.clip(cols - c0, 0.0, 1.0)
        g = self._grid
        top = g[r0, c0] * (1 - fc) + g[r0, c0 + 1] * fc
        bottom = g[r0 + 1, c0] * (1 - fc) + g[r0 + 1, c0 + 1] * fc
        return top * (1 - fr) + bottom * fr


class Terrain:
    """Ground height on the WGS84 ellipsoid: DEM orthometric height plus geoid undulation."""

    def __init__(
        self, root: Path | None = None, dem: Dem | None = None, geoid: Geoid | None = None
    ) -> None:
        self.dem = dem or Dem(root)
        self.geoid = geoid or Geoid(root)

    def ellipsoidal_height(self, lat: ArrayLike, lon: ArrayLike) -> FloatArray:
        """Ground height above the ellipsoid, m."""
        return self.dem.elevation(lat, lon) + self.geoid.undulation(lat, lon)
