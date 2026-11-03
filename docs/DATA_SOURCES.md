# Data sources

See the table in [HANDOFF.md](HANDOFF.md), section "Data sources and licensing". Defaults are
GOES GLM (lightning, NOAA open data) and adsb.lol globe_history (aircraft, ODbL 1.0). Blitzortung
and LightningMaps are never used. Nothing is bundled: loaders download into a local cache under
`data/`, which is gitignored.

Loader status: none yet. GLM arrives in M2, adsb.lol and aircraft types in M3.

Until then, `earwitness simulate` writes synthetic source data next to each case:
`strikes.json` (lightning events with UTC time, latitude, longitude, kind) and `tracks.json`
(one ADS-B-like trace per synthetic aircraft at 1 s). The M2 and M4 pipelines accept these files
in place of downloaded data so synthetic end-to-end cases run without network access.
