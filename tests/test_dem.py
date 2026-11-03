"""DEM and geoid sampling on synthetic GeoTIFF rasters."""

import numpy as np
import rasterio
from rasterio.transform import from_origin

from earwitness.data import dem as dm


def _write_tile(path, west, north, nx, ny, dx, dy, values, nodata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=ny,
        width=nx,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(west, north, dx, dy),
        nodata=nodata,
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def test_tile_ids():
    assert dm.tile_id(35.5, -97.5) == "Copernicus_DSM_COG_10_N35_00_W098_00_DEM"
    assert dm.tile_id(-1.2, 10.9) == "Copernicus_DSM_COG_10_S02_00_E010_00_DEM"
    assert dm.tile_id(0.0, 0.0) == "Copernicus_DSM_COG_10_N00_00_E000_00_DEM"


def test_dem_elevation_from_a_cached_tile(tmp_path):
    # A 1-degree tile at 0.1-degree cells whose value is 1000 * (row index) + col index.
    nx = ny = 10
    values = np.arange(ny)[:, None] * 1000.0 + np.arange(nx)[None, :]
    tile = dm.tile_id(35.5, -97.5)
    _write_tile(
        dm.tile_path(tmp_path, tile), -98.0, 36.0, nx, ny, 0.1, 0.1, values, nodata=-32767.0
    )
    d = dm.Dem(tmp_path, allow_download=False)
    # (35.95, -97.95) is row 0, col 0; (35.05, -97.05) is row 9, col 9.
    np.testing.assert_allclose(d.elevation([35.95, 35.05], [-97.95, -97.05]), [0.0, 9009.0])
    # A point in a tile that is not cached (and cannot be downloaded) reads as sea level.
    np.testing.assert_allclose(d.elevation(10.5, 10.5), [0.0])
    assert dm.tile_id(10.5, 10.5) not in d.missing  # downloads disabled, nothing marked missing
    d.close()


def test_geoid_bilinear_interpolation(tmp_path):
    # A global 1-degree grid starting at lon 0 (0..360 layout) with undulation = lat + lon / 100.
    lons = np.arange(0.5, 360.0, 1.0)
    lats = np.arange(89.5, -90.0, -1.0)
    values = lats[:, None] + lons[None, :] / 100.0
    path = tmp_path / "egm.tif"
    _write_tile(path, 0.0, 90.0, lons.size, lats.size, 1.0, 1.0, values)
    g = dm.Geoid(path=path)
    np.testing.assert_allclose(
        g.undulation([35.5, 35.0], [10.5, 10.0]), [35.5 + 0.105, 35.0 + 0.10], atol=1e-6
    )
    # Negative longitudes wrap into the 0..360 grid.
    np.testing.assert_allclose(g.undulation(35.5, -97.5), [35.5 + 262.5 / 100.0], atol=1e-6)


def test_terrain_sums_dem_and_geoid(tmp_path):
    nx = ny = 10
    tile = dm.tile_id(35.5, -97.5)
    _write_tile(
        dm.tile_path(tmp_path, tile), -98.0, 36.0, nx, ny, 0.1, 0.1, np.full((ny, nx), 300.0)
    )
    lons = np.arange(0.5, 360.0, 1.0)
    lats = np.arange(89.5, -90.0, -1.0)
    gpath = tmp_path / "egm.tif"
    _write_tile(
        gpath, 0.0, 90.0, lons.size, lats.size, 1.0, 1.0, np.full((lats.size, lons.size), -25.0)
    )
    terrain = dm.Terrain(dem=dm.Dem(tmp_path, allow_download=False), geoid=dm.Geoid(path=gpath))
    np.testing.assert_allclose(terrain.ellipsoidal_height(35.5, -97.5), [275.0])
