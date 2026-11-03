# Earwitness

**Does my recording contain location clues?**

Earwitness is an open-source proof of concept that estimates where a recording was made from
background sounds that public databases also log with precise time and position: thunder, matched
against lightning records, and the Doppler whine of propeller aircraft, matched against ADS-B
flight records. It is a clean-room reconstruction of the method colsto demonstrated on YouTube in
2026 (about 5 km for thunder with free data, about 100 m for aircraft).

Site: [earwitness.dev](https://earwitness.dev). Full specification: [docs/HANDOFF.md](docs/HANDOFF.md).

## Status

Pre-alpha. Nothing here locates real recordings yet.

| Milestone | Scope | State |
| --- | --- | --- |
| M0 | Scaffold, CI, packaging | done |
| M1 | Physics core, forward models, synthetic recordings (`earwitness simulate`) | done |
| M2 | Thunder pipeline on GOES GLM | next |
| M3 | ADS-B data (adsb.lol) | planned |
| M4 | Aircraft Doppler pipeline | planned |
| M5 | Desktop UI | planned |
| M6 | Real-world validation | planned |
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
