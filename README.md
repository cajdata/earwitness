# Earwitness

**Does my recording contain location clues?**

Earwitness is an open-source proof of concept that estimates where a recording was made from
background sounds that public databases also log with precise time and position: thunder, matched
against lightning records, and the Doppler whine of propeller aircraft, matched against ADS-B
flight records. It is a clean-room reconstruction of the method colsto demonstrated on YouTube in
2026 (about 5 km for thunder with free data, about 100 m for aircraft).

Site: [earwitness.dev](https://earwitness.dev). Source: [github.com/cajdata/earwitness](https://github.com/cajdata/earwitness).
Full specification: [docs/HANDOFF.md](docs/HANDOFF.md).

## Status

Pre-alpha. Both methods run end to end on real data (GOES GLM lightning, adsb.lol flights)
and pass their synthetic acceptance checks, and the desktop UI takes a recording from a file
to a heat map for both; no real recording has been validated yet (that is M6).

| Milestone | Scope | State |
| --- | --- | --- |
| M0 | Scaffold, CI, packaging | done |
| M1 | Physics core, forward models, synthetic recordings (`earwitness simulate`) | done |
| M2 | Thunder pipeline on GOES GLM: loader, search, refinement, outputs | done, see [docs/VALIDATION.md](docs/VALIDATION.md) |
| M3 | ADS-B data: adsb.lol loader, aircraft types, terrain | done |
| M4 | Aircraft Doppler pipeline: events, funnel, scoring, outputs | done, see [docs/VALIDATION.md](docs/VALIDATION.md) |
| M5 | Desktop UI: local API, Vite front end, native window, audit mode | done, see [docs/VALIDATION.md](docs/VALIDATION.md) |
| M6 | Real-world validation | next |
| M7 | v0.1.0 release and site | planned |

## Responsible use

This project exists to show that ordinary recordings leak location, to let people check their
own recordings, and to document what does and does not prevent it. The rules it follows:

- The audit mode ("does my recording contain location clues?") leads the UI and the docs.
- Every example and default workflow uses your own recordings or synthetic cases. The project
  publishes no results that locate other people's recordings.
- No hosted service, no downloading media from URLs, no bundled third-party datasets.
- Blitzortung and LightningMaps data are never used; their terms do not allow it.
- Countermeasures that work and do not work will be documented in `docs/COUNTERMEASURES.md`.

## Quick start

Requires [uv](https://docs.astral.sh/uv/). ffmpeg on `PATH` is optional; it enables `--codec`
in the simulator and decoding of non-WAV input.

```bash
uv sync
```

```bash
uv run earwitness --help
```

Check a recording of your own for location clues. The audit report says which clues it
contains, what each could reveal, and what has been shown to help:

```bash
uv run earwitness audit recording.wav
```

Open the desktop UI: a local web app in a native window (`--browser` opens it in the system
browser instead). It walks through audit, setup (file, method, dates, a search region drawn
on a map), audio (spectrogram with band zoom, Doppler fit sliders, trace tool and silence
windows for aircraft; the loudness envelope for thunder), run (cached data, options, a
compute estimate, live log) and results (heat map, ranked list, score histogram, observed
versus modeled overlays). Everything runs on this computer, on a random localhost port
guarded by a session token.

```bash
uv run earwitness ui
```

From a source checkout, build the front end once first (Node 22; wheels from PyPI carry it):

```bash
cd ui && npm install && npm run build
```

Generate synthetic test recordings with a known answer:

```bash
uv run earwitness simulate aircraft --lat 35.2 --lon -97.4 --start 2026-09-08T18:00:00Z --minutes 20 --out case-aircraft
```

```bash
uv run earwitness simulate thunder --lat 35.2 --lon -97.4 --start 2026-07-02T21:00:00Z --minutes 30 --out case-thunder
```

Each case folder holds `recording.wav`, `truth.json` (location, start time, events, physics and
mixing parameters) and the synthetic source data the search will consume later (`tracks.json`
or `strikes.json`).

Locate a thunder recording against real GOES GLM lightning data. The first run downloads the
hours it needs into `data/cache` (about 110 MB per hour) and the Natural Earth land mask:

```bash
uv run earwitness analyze thunder recording.wav --date 2025-05-16 --time-window 2025-05-16T21:00:00Z 2025-05-17T00:00:00Z --bbox=-97,33,-82,42 --out runs/example
```

Or fetch and export the flashes first:

```bash
uv run earwitness data glm --date 2025-05-16 --hours 21-23 --bbox=-97,33,-82,42 --export flashes.parquet
```

Cache one day of adsb.lol traces for a region (about 4 GB downloaded, a few hundred MB kept):

```bash
uv run earwitness data adsb --date 2026-09-08 --bbox=-125,24,-66,50
```

Locate a recording with two propeller flyovers against that day. The measured events (ranges
for each flyover's heard closest-approach time, rest frequency, speed and distance, plus
silence windows) come from an `events.json` file; its layout is documented at the top of
`src/earwitness/audio/events.py`, and the simulator's `truth.json` can be turned into one for
testing. With one flyover and a known recording start time, add `--known-time`:

```bash
uv run earwitness analyze aircraft recording.wav --events events.json --date 2026-09-08 --bbox=-100,30,-85,42 --out runs/aircraft
```

Write `--bbox=W,S,E,N` with the equals sign, or give the four numbers as separate arguments,
so a negative western longitude is not mistaken for an option. Each analysis run writes a folder
with `results.parquet`, `heatmap.geojson`, per-candidate overlays, `config.toml`,
`summary.json` and a run log (the thunder run also writes `histogram.json` and `priors.json`).

## How it works

The recording is the receiver, the lightning strikes or aircraft are the satellites, and the
unknown recording start time is the receiver clock bias. For any candidate location, physics
predicts what that spot would have heard and when. The search predicts the signal for every
candidate and every start time, scores it against the recording, and the best score is the
location. Audio is never inverted into a location directly.

## Data sources and attribution

Nothing is bundled. Loaders download into a local cache (`data/`, gitignored).

| Source | Role | License |
| --- | --- | --- |
| [NOAA GOES GLM](https://registry.opendata.aws/noaa-goes/) | Lightning (Americas) | NOAA open data |
| [MET Norway Frost](https://frost.met.no) | Lightning (Nordic), adapter | CC BY 4.0 / NLOD |
| [adsb.lol globe_history](https://www.adsb.lol/docs/open-data/historical/) | Aircraft tracks | ODbL 1.0 |
| USGS 3DEP, Copernicus GLO-30 | Terrain | Public domain; free with attribution |
| Natural Earth | Land mask | Public domain |
| OpenStreetMap | Basemap tiles | ODbL; tile usage policy applies |

## Development

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format .
```

Conventions are in [CONTRIBUTING.md](CONTRIBUTING.md). The build order and acceptance checks are in
[docs/HANDOFF.md](docs/HANDOFF.md).

## License

Apache-2.0. See [LICENSE](LICENSE).

## Acknowledgements

The method is colsto's: [thunder, Blitzortung](https://www.youtube.com/watch?v=SnbkIm56TfI),
[thunder, MET Norway](https://www.youtube.com/watch?v=6a7Z0I3LAnE), and
[propeller-aircraft Doppler](https://www.youtube.com/watch?v=UytYrhYLOKI). His code is not public;
everything here is reconstructed from the videos.
