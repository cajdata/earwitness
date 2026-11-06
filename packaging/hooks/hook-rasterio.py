"""PyInstaller hook for rasterio, which has none of its own or in pyinstaller-hooks-contrib.

rasterio's compiled modules import Python ones the analysis cannot see (rasterio._base imports
rasterio.serde, for one), and the wheel's GDAL and PROJ support files must sit beside the
package, where rasterio.env looks for them: without them the frozen program fails as soon as
it reads a terrain tile.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules(
    "rasterio", filter=lambda name: not name.startswith("rasterio.rio")
)
datas = collect_data_files("rasterio", subdir="gdal_data") + collect_data_files(
    "rasterio", subdir="proj_data"
)
