# Data sources

See the table in [HANDOFF.md](HANDOFF.md), section "Data sources and licensing". Defaults are
GOES GLM (lightning, NOAA open data) and adsb.lol globe_history (aircraft, ODbL 1.0). Blitzortung
and LightningMaps are never used. Nothing is bundled: loaders download into a local cache under
`data/`, which is gitignored.

## Loader status

| Source | Module | State |
| --- | --- | --- |
| GOES GLM L2 LCFA | `earwitness.data.glm` | Done (M2). Anonymous S3 listing and download, one Parquet per satellite-hour, quality-flag, time and bbox filters. |
| Natural Earth land | `earwitness.data.landmask` | Done (M2). 1:50m by default, 1:10m optional. |
| adsb.lol globe_history | | M3 |
| Aircraft types (tar1090-db, ICAO designators) | | M3 |
| DEM (3DEP, Copernicus GLO-30) | | M3 |
| MET Norway Frost | | Adapter after v0.1 |

## Cache layout

The cache root is `--cache DIR`, else `EARWITNESS_CACHE`, else `data/cache`.

- `glm/<SAT>/<YYYY>/<DDD>/<HH>.parquet`: flashes of one satellite-hour with columns `time`
  (UTC timestamp), `lat`, `lon`, `energy_j`, `area_m2`, `quality`, `flash_id`, `satellite`.
  A full day is 4,320 NetCDF files (about 2.6 GB downloaded) that become 24 Parquet files of a
  few MB each; the raw files are deleted after conversion.
- `landmask/ne_<scale>_land.geojson`: Natural Earth land polygons.

## GLM notes

- Satellite choice: GOES-East is GOES-16 until 2025-04-06 and GOES-19 from 2025-04-07; GOES-West
  is GOES-17 until 2023-01-03 and GOES-18 from 2023-01-04. `auto` picks East for regions whose
  center is east of 110 W and West otherwise; both satellites see the whole contiguous US.
- Each flash record carries the flash centroid and the time of its first event. GLM is optical,
  so flashes include in-cloud lightning and no cloud-to-ground classification is available.
- Flashes with a nonzero quality flag are dropped unless `--all-quality` is given.

## Synthetic source files

`earwitness simulate` writes synthetic source data next to each case: `strikes.json`
(lightning events with UTC time, latitude, longitude, kind) and `tracks.json` (one ADS-B-like
trace per synthetic aircraft at 1 s). `analyze thunder --strikes FILE` accepts `strikes.json`,
a GLM Parquet export, or any JSON or Parquet file with time, latitude and longitude columns, so
synthetic end-to-end cases run without network access.
