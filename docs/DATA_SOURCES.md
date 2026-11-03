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
| adsb.lol globe_history | `earwitness.data.adsb_lol` | Done (M3). Daily release parts streamed as one tar, traces parsed, clipped to a region and day, one Parquet per day and region. |
| Aircraft types | `earwitness.data.aircraft_types` | Done (M3). dump1090's designator table (about 2,600 types); a hand-placed ICAO DOC 8643 export takes precedence. |
| Terrain and geoid | `earwitness.data.dem` | Done (M3). Copernicus GLO-30 tiles from AWS plus the EGM96 grid from the PROJ CDN. |
| MET Norway Frost | | Adapter after v0.1 |

## Cache layout

The cache root is `--cache DIR`, else `EARWITNESS_CACHE`, else `data/cache`.

- `glm/<SAT>/<YYYY>/<DDD>/<HH>.parquet`: flashes of one satellite-hour with columns `time`
  (UTC timestamp), `lat`, `lon`, `energy_j`, `area_m2`, `quality`, `flash_id`, `satellite`.
  A full day is 4,320 NetCDF files (about 2.6 GB downloaded) that become 24 Parquet files of a
  few MB each; the raw files are deleted after conversion.
- `landmask/ne_<scale>_land.geojson`: Natural Earth land polygons.
- `adsb/<variant>/<YYYY-MM-DD>/<region>.parquet`: one day of adsb.lol points inside a region
  (columns `icao`, `time`, `lat`, `lon`, `alt_baro_ft`, `alt_geom_ft`, `on_ground`, `gs_kt`,
  `track_deg`, `vrate_fpm`, `flags`, `source`, `type_code`, `registration`, `description`,
  `db_flags`). The split tar parts (about 4 GB per day) sit in `adsb/_raw/<day>/` while
  converting and are deleted afterwards unless `--keep-raw` is given.
- `aircraft_types/icao_aircraft_types.json`: designator to ICAO description (L1P, H2T, ...).
- `dem/copernicus_glo30/<tile>.tif`: 1-degree Copernicus GLO-30 tiles (about 40 MB each);
  `dem/egm96_15.tif`: the EGM96 geoid grid (2.7 MB).

## GLM notes

- Satellite choice: GOES-East is GOES-16 until 2025-04-06 and GOES-19 from 2025-04-07; GOES-West
  is GOES-17 until 2023-01-03 and GOES-18 from 2023-01-04. `auto` picks East for regions whose
  center is east of 110 W and West otherwise; both satellites see the whole contiguous US.
- Each flash record carries the flash centroid and the time of its first event. GLM is optical,
  so flashes include in-cloud lightning and no cloud-to-ground classification is available.
- Flashes with a nonzero quality flag are dropped unless `--all-quality` is given.

## adsb.lol notes

- One day is a GitHub release of `adsblol/globe_history_<year>` tagged
  `v<YYYY>.<MM>.<DD>-planes-readsb-prod-0`, split into 2 GB parts. The loader streams the parts
  through `tarfile` without writing the reassembled archive.
- Each `traces/<xx>/trace_full_<icao>.json` is gzip-compressed readsb trace JSON. Points carry a
  stale flag, a new-leg flag, barometric and (when broadcast) geometric altitude, ground speed,
  track and the position source. Addresses starting with `~` are non-ICAO (TIS-B) targets.
- adsb.lol also serves the same per-aircraft file for any past day at
  `https://adsb.lol/globe_history/<YYYY>/<MM>/<DD>/traces/<xx>/trace_full_<icao>.json`, which
  is what its replay view loads; `scripts/m3_spotcheck.py` uses it to verify the cache.
- Geometric altitude is height above the WGS84 ellipsoid; barometric altitude is pressure
  altitude. `earwitness.aircraft.prep` prefers geometric and shifts barometric by each leg's
  median geometric-minus-baro offset where both exist.

## Synthetic source files

`earwitness simulate` writes synthetic source data next to each case: `strikes.json`
(lightning events with UTC time, latitude, longitude, kind) and `tracks.json` (one ADS-B-like
trace per synthetic aircraft at 1 s). `analyze thunder --strikes FILE` accepts `strikes.json`,
a GLM Parquet export, or any JSON or Parquet file with time, latitude and longitude columns, so
synthetic end-to-end cases run without network access.
