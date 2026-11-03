# Audio Geolocation PoC: Project Handoff

As of 2026-11-03 · Chris Johnson

## Purpose and scope

Build Earwitness, an open-source desktop app (Python, Windows first, Linux second) that estimates where a recording was made by matching background thunder or propeller-aircraft sound against public lightning and ADS-B records. It is a clean-room proof of concept of the method colsto demonstrated on YouTube in 2026. His code is not public, so everything here is reconstructed from his three videos.

**Project name: Earwitness.** An earwitness is someone who testifies about what they heard. Domain: [earwitness.dev](https://earwitness.dev), registered. Python package and CLI command: `earwitness`. The PyPI name was unregistered as of Nov 3, 2026; registering it is an M1 task (section 7).

**In scope**

- Two methods: thunder (kilometer-level) and aircraft Doppler (about 100 m).
- Input: a local audio or video file plus priors the user supplies: date or date range (UTC), optional search region, optional known clock time.
- Output: ranked candidate locations, a heat map, and per-candidate overlays of modeled vs observed signal.
- An audit mode that reports which location clues a recording contains, and a forward simulator that generates synthetic test recordings.

**Out of scope for the PoC**

- Mobile, a hosted service, real-time monitoring, bulk processing of other people's media.
- Bundling or redistributing third-party datasets. Users download data at runtime into a local cache.
- Downloading media from URLs. Local files only.

**How to use this doc.** Save it as `docs/HANDOFF.md` in the repo and point `CONTRIBUTING.md` at it. Sections 3 and 4 are the method spec, section 7 is the build order, and section 12 covers starting the project. Numbers marked "per video" come from colsto's videos: treat them as starting defaults, not validated constants.

## Background: the method

colsto showed that background sound plus public event logs can locate a recording: thunder to about 5 km with free data, propeller aircraft to about 100 m.

| Video | Published | Length | Clue and data | Result |
| --- | --- | --- | --- | --- |
| [I discovered a sound that leaks your exact location](https://www.youtube.com/watch?v=UytYrhYLOKI) | Nov 2, 2026 | 29:12 | Prop aircraft Doppler curves, ADS-B | About 100 m. Also solved from a 1-minute clip with a known time. |
| [I located a fan's video using thunder sounds](https://www.youtube.com/watch?v=6a7Z0I3LAnE) | Sep 30, 2026 | 11:11 | Thunder, MET Norway lightning (about 200 m strike accuracy), 4-minute clip | About 2.2 km off. Clustered strikes limited precision. |
| [Can you geolocate a video using background thunder sounds?](https://www.youtube.com/watch?v=SnbkIm56TfI) | Aug 9, 2026 | 14:47 | Thunder, Blitzortung lightning (1 to 5 km accuracy), 30-minute clip | Whole-world search picked the right city; a finer re-run put the best point within 5 km. |

**Core idea.** Some background sounds come from events that public databases log with precise time and position. For any candidate location, physics predicts what that spot would have heard and when. Predict the signal for every candidate, score it against the recording, and the best score is the location. Never try to invert audio into a location directly.

**Mental model: GPS run with sound.** The recording is the receiver, the lightning strikes or aircraft are the satellites, and the unknown recording start time is the receiver clock bias. The search sweeps every start time, so what pins the location is the spacing between events, not their absolute time. Geometry matters the same way it does in GPS: sources clustered in one direction give a long ridge of equally good candidates instead of a point.

**What each method trades.** Thunder gives many simple events (a loudness spike each) and is limited by how accurately strikes are located. Aircraft give few events, but each Doppler curve encodes speed, closest-approach distance, and the plane's actual maneuvering, and ADS-B positions are precise. The aircraft method needs two flyovers, or one flyover plus a known recording time, or a small search region.

## Method spec: thunder

The thunder pipeline compares the recording's low-frequency loudness envelope against a modeled envelope for every candidate point and every time offset, scored with sliding cosine similarity.

**Inputs.** Recording; date or date range (UTC); optional region polygon; optional rough time window. Lightning events need time (ms precision), latitude, longitude, and optionally type (cloud-to-ground or in-cloud) and energy.

1. **Observed envelope.** Decode to mono, resample to 2 kHz, band-pass 20 to 250 Hz (per video; zero-phase Butterworth), RMS over 0.25 s frames, Gaussian smoothing (about 5 s per video's UI, tunable), then resample to the sweep step. Flag likely wind noise, which sits in the same band.
2. **Event selection.** Strikes within the audible radius `R_max` of the search area over the date range plus clip length. Default `R_max` = 25 km, tunable.
3. **Coarse grid.** 5 km spacing (per video). Keep land points (sea optional) with at least `N` strikes inside `R_max` in some window of clip length. Video 1 used `N` = 10 for a clip with about 15 audible strikes; default `N` = max(3, round(0.6 x observed peak count)).
4. **Forward model** per candidate (formulas below): arrival time from 3D slant distance, loudness from inverse-square falloff with a near-field cap, each strike rendered as a pulse kernel, then the same smoothing as the observed side.
5. **Score.** Sliding cosine similarity of observed vs modeled across all offsets in the date range, at a 5 s step for the coarse pass (per video: about 8,000 offsets per point per day, about 90,000 points, about 700 million comparisons for the whole world). Keep the best score, best offset, and the top 3 offsets per candidate.
6. **Refine.** Re-run the top regions at 1 km, then 200 m, with a 1 s step.
7. **Output.** Candidate table (Parquet), GeoJSON heat map, score histogram, and per-candidate observed vs modeled overlays.

**Formulas.** Arrival time, with `h` = 2,000 m source height for cloud-to-ground strikes (per video) and `c` = 343 m/s (optionally temperature-adjusted):

```latex
t_{arr} = t_{strike} + \frac{\sqrt{d_h^2 + h^2}}{c}
```

Modeled loudness and envelope, with `p` = 2 (per video), a cap so close strikes saturate like a clipped mic, and kernel `k` a fast-attack exponential decay (rise about 0.5 s, decay 3 to 5 s, tunable; distant thunder rumbles longer):

```latex
A_i = \min\left(A_{cap},\ (R_0 / R_i)^p\right), \qquad m(t) = \sum_i A_i \, k(t - t_{arr,i})
```

Sliding cosine score, where the denominator uses only the model energy inside the clip-length window at offset tau:

```latex
s(\tau) = \frac{\sum_t o(t)\, m(t+\tau)}{\lVert o \rVert \, \lVert m_{[\tau,\, \tau+L]} \rVert}
```

**Implementation notes.**

- Compute the numerator for all offsets with one FFT cross-correlation and the denominator with a cumulative sum of m squared. That is O(n log n) per candidate.
- Cosine similarity is scale-invariant, so the per-window "normalize to the loudest strike" step in the video does not change the score. Keep it for display only.
- Clustered storms produce ridge-shaped ambiguity (video 2). Add an optional weighting that boosts strikes whose bearing from the candidate differs from the dominant cluster.
- Dense storms with free data create attribution ambiguity: with 1 to 5 km strike error (3 to 15 s of timing error), the model cannot tell which strike matches which clap.

## Method spec: aircraft Doppler

The aircraft pipeline measures each propeller-plane Doppler curve in the spectrogram, narrows candidates with cheap physical filters, then models the exact curve each surviving candidate would have heard from the real ADS-B track and scores it against the spectrogram.

**Inputs.** Recording; date (UTC); optional region; optional known recording clock time. ADS-B traces need time, latitude, longitude, geometric and barometric altitude, ground speed, and track, plus an aircraft-type lookup.

1. **Spectrogram.** STFT with a long window for frequency resolution (video 3 used 8192-sample Hamming), displayed 0 to 500 Hz on a log scale, with per-frequency median whitening so faint lines show. The curves are inaudible and only visible zoomed into this band.
2. **Event measurement.** For each flyover, fit the straight-line constant-velocity model (formula below) to get ranges for fundamental frequency `f0`, speed `v`, closest-approach slant distance `d`, and closest-approach time `t0` in recording time. Store ranges, not points: video 3 could say the plane was near 100 m/s but surely not 70. Also mark silence windows where no flyover is heard. The PoC does this with sliders in the UI; automatic detection is a stretch goal.
3. **Flight prep.** Load the day, clip to region and time, keep piston and turboprop types (ICAO type description letter P or T; drop J; helicopters optional), interpolate each track to 1 s in local ENU coordinates, compute 3D velocity, and put aircraft and ground heights on one vertical datum using the DEM.
4. **Speed gate.** Per event, keep flight segments whose speed near closest approach falls in that event's `v` range.
5. **Distance tube.** For each surviving segment, lay candidates on a 200 m grid only where the closest-approach slant distance to the track is inside the event's `d` range, using DEM ground elevation. Hard cap 10 km (per video).
6. **Timing pair.** Keep candidates that sit in an event-1 tube and an event-2 tube whose heard closest-approach times differ by the observed gap within tolerance (video 3: 920 s plus or minus 5 s). Implement as a spatial join on grid cell, then a time-difference filter. Single-event mode (known clock time) replaces this with: heard closest approach within tolerance of the known time.
7. **Silence filter.** Drop candidates that would have heard any other prop flight inside a silence window (closest approach under a conservative audible distance, default 4 km, tunable). Silence is a signal: it removes most airport-adjacent candidates.
8. **Exact curve model** per candidate and flight from the interpolated ADS-B track, using emission time (formulas below).
9. **Score.** Sum whitened spectrogram brightness under each modeled curve (optionally its harmonics). Event 1 sweeps a small time correction (plus or minus 3 s) and `f0` (plus or minus 3 Hz) and keeps the best. Event 2 sweeps `f0` only: its timing relative to event 1 is fixed by flight geometry, so a misplaced event 2 means a bad candidate. Combined score = event 1 + event 2. In single-event mode, the user traces the faint curve and the score is closeness to that trace.
10. **Refine and group.** Expand survivors to a 100 m grid (a 200 m point can be up to 141 m off and score badly, per video), group by flight pair, report the best point per pair, then run a dense heat map for the top pairs.

**Formulas.** Straight-line fit model used for measurement (step 2), with emission time `t_e` and reception time `t_r`:

```latex
r(t_e) = \sqrt{d^2 + v^2 (t_e - t_0)^2}, \quad v_r = \frac{v^2 (t_e - t_0)}{r(t_e)}, \quad t_r = t_e + \frac{r(t_e)}{c}
```

Exact model from the real track (step 8), with aircraft position `p(t)` and observer `o`. Solve emission time by fixed-point iteration (3 to 5 iterations converge because v is much smaller than c):

```latex
t_e = t_r - \frac{\lVert p(t_e) - o \rVert}{c}, \qquad v_r = \frac{\dot{p}(t_e) \cdot (p(t_e) - o)}{\lVert p(t_e) - o \rVert}
```

Doppler shift for a moving source and stationary observer, with radial velocity negative while approaching:

```latex
f(t_r) = f_0 \, \frac{c}{c + v_r(t_e)}
```

**Required unit tests from the video's worked example.** f0 = 130 Hz and v_r = -57.97 m/s must give 156.44 Hz. Using the plane's position at reception time instead of emission time must produce a differently shaped curve, not just a delayed one.

**Reference counts from video 3** (sanity checks, not targets): about 35,000 candidates after the timing filter in a test area under 0.05% of the continental US; about 73,000 US-wide after speed and distance; about 15,000 after the silence filter; 30,000 to 40,000 after 100 m expansion. The winning flight pair scored 20.48 against 15.25 for the runner-up.

## Data sources and licensing

Default sources are GOES GLM for lightning and adsb.lol for aircraft, both free and usable by an open-source app. Blitzortung and OpenSky are excluded as defaults because their terms do not allow it.

| Source | Role | Coverage and precision | Access | License | Status |
| --- | --- | --- | --- | --- | --- |
| [GOES GLM L2 (NOAA, AWS)](https://registry.opendata.aws/noaa-goes/) | Lightning, primary | Americas. Typical location error about 3 to 4 km, timing about 1 ms, 20 s files ([validation](https://noaasis.noaa.gov/pdf/ps-pvr/goes19/GLM/Provisional/GLM19_PROV_PS-PVR_13Mar2025.pdf)). Optical: includes in-cloud flashes and reports flash centroids, not ground strike points. | Anonymous S3, `GLM-L2-LCFA` in the `noaa-goes16/18/19` buckets | NOAA open data | Default |
| [MET Norway](https://api.met.no) via [Frost](https://frost.met.no) | Lightning, Nordic | Norway, Sweden, Denmark and neighbors. About 200 m per video 2. | Free client ID | CC BY 4.0 / NLOD | Adapter. Confirm the lightning endpoint and archive depth. |
| Vaisala GLD360/NLDN, Earth Networks ENTLN, Xweather | Lightning, optional | About 100 m class per video 1 | Paid | Commercial | Plug-in adapter interface only |
| [Blitzortung / LightningMaps](https://www.lightningmaps.org/about) | Excluded | 1 to 5 km | Raw data limited to participants or by permission; third-party apps must not pull from their servers ([forum policy](https://forum.blitzortung.org/showthread.php?pid=17453)) | CC BY-SA 4.0 | Do not ship. Never tap the LightningMaps websocket. |
| [adsb.lol globe_history](https://www.adsb.lol/docs/open-data/historical/) | Aircraft, primary | Global, community receivers. One gzipped JSON trace per aircraft per day. Gigabytes per day. | Daily GitHub releases, split tar files | ODbL 1.0 | Default |
| [OpenSky Trino](https://openskynetwork.github.io/opensky-api/trino.html) | Aircraft, optional | Global, research grade | Only university researchers, government, aviation authorities; others need a license | Research or commercial | Optional adapter for users who already have access |
| [Bellingcat Turnstone](https://github.com/bellingcat/adsb-history) | Reference code | Parses adsb.lol releases | GitHub | MIT | Read before writing the loader |
| tar1090-db plus ICAO type designators | Aircraft type | icao24 to type; ICAO description codes like L1P, L2T, L2J give engine class | GitHub | Check before use | Required |
| USGS 3DEP, Copernicus GLO-30 | Terrain (DEM) | US 10 to 30 m; global 30 m | Free download | Public domain; free with attribution | Required |
| Natural Earth | Land mask | Global | Free | Public domain | Required |
| OpenStreetMap tiles | Basemap | Global | Tile servers | ODbL, tile usage policy | Configurable tile URL |

**Rules for the repo.**

- Ship no third-party datasets. The app downloads into a local cache (per-day, per-region Parquet), and only tiny synthetic fixtures live in `tests/`.
- Show source attribution in the UI and README.
- If a processed ADS-B database is ever published, it must be released under ODbL with attribution.
- GOES-19 replaced GOES-16 as GOES-East in 2025; resolve the right satellite and bucket per date.

## Architecture

A Python engine with a CLI comes first, so every stage is testable headless. The desktop UI is a thin local web front end over the same engine.

```
Simulator -> Recording -> Audio features ----+
Priors --------------------------------------+--> Search engine --> Results
Public data -> Local cache ------------------+    (forward models,    |
                                                   grid and funnel,    |
                                                   time sweep,         |
                                                   scoring)            |
                                                        ^              |
                                CLI and desktop UI -----+ (runs)       |
                                        ^------------------------------+ (shows)
```

Real or simulated recordings and the user's priors feed one search engine. Public data is downloaded and cached locally first, and the CLI and desktop UI drive the same engine.

**Stack.**

- Python 3.12, `uv`, `ruff`, `pytest`, type hints throughout.
- Math and signal: numpy, scipy, numba. Optional CuPy later.
- Data: polars, DuckDB, pyarrow (Parquet cache), xarray with h5netcdf (GLM NetCDF), rasterio (DEM), pyproj, shapely, h3 (resolution 9 for the 200 m stage, 10 for 100 m).
- Audio: ffmpeg via subprocess for decode, soundfile for WAV.
- UI: FastAPI backend; TypeScript front end built with Vite; MapLibre GL JS for maps and heat maps; Plotly.js for spectrograms and curve overlays; wrapped in pywebview as a desktop window.
- Packaging: PyInstaller for a Windows build; `pipx install` for Linux.

**Repo layout.**

```
earwitness/
  pyproject.toml
  CONTRIBUTING.md
  docs/HANDOFF.md, METHOD.md, DATA_SOURCES.md, COUNTERMEASURES.md
  src/earwitness/
    core/        geodesy.py (ECEF/ENU, slant range), physics.py (sound speed, Doppler, emission time), grid.py
    audio/       decode.py, envelope.py, spectrogram.py, events.py (Doppler fit, traces, silence windows)
    data/        cache.py, glm.py, metno.py, adsb_lol.py, aircraft_types.py, dem.py, landmask.py, adapters.py
    thunder/     model.py, search.py, refine.py
    aircraft/    prep.py, funnel.py, model.py, score.py, refine.py
    simulate/    thunder_synth.py, aircraft_synth.py, mix.py (noise, codecs, countermeasures)
    audit/       report.py
    project.py   # analysis folder: priors, events, results, config, run log
    cli.py
    server/      FastAPI app
  ui/            # Vite + TypeScript front end
  tests/         unit, synthetic end-to-end, tiny fixtures
  data/          # gitignored: cache and personal recordings
```

**CLI.**

```
earwitness data glm --date 2026-07-02 --bbox <w,s,e,n>
earwitness data adsb --date 2026-09-08 --bbox <w,s,e,n>
earwitness simulate thunder --lat <lat> --lon <lon> --start <utc> --minutes 30 --out case/
earwitness simulate aircraft --lat <lat> --lon <lon> --start <utc> --minutes 20 --out case/
earwitness analyze thunder <file> --date <d> [--region <geojson>] [--out run/]
earwitness analyze aircraft <file> --events events.json [--known-time <utc>] [--out run/]
earwitness audit <file>
earwitness ui
```

**Analysis project folder.** Each run is a folder with the recording hash, priors, `events.json`, `config.toml`, `results.parquet`, `heatmap.geojson`, and a run log, so any result can be reproduced from its folder.

**UI views.** Project setup (file and priors); spectrogram with band zoom, Doppler fit sliders (`f0`, `v`, `d`, `t0`), trace tool, and silence-window marking; envelope view for thunder; run panel with progress and a compute estimate; results with map heat map, ranked list, score histogram, and observed vs modeled overlay per candidate; audit report.

## Milestones

Build in this order: simulator and physics first, then thunder end to end, then aircraft, then UI. Each milestone ends with its acceptance check passing in CI. Accuracy and runtime numbers below are initial targets, to be revised after M2.

### M0: Scaffold

- [ ] Repo, `pyproject.toml` with `uv`, package layout from section 6, `earwitness --help` works
- [ ] GitHub Actions: ruff and pytest on Windows and Linux
- [ ] LICENSE, README stub with purpose and responsible-use statement, CONTRIBUTING.md, `.gitignore` covering `data/`
- [ ] PyPI account with 2FA, plus a pending trusted publisher for `earwitness` tied to the repo's release workflow

Done when: CI is green on both OSes with a placeholder test.

### M1: Physics core and forward simulator

- [ ] Geodesy: WGS84 to ECEF to local ENU, slant range with terrain heights
- [ ] Thunder forward model: arrival time, capped loudness, pulse kernel, envelope
- [ ] Aircraft forward model: track interpolation, emission-time solver, radial velocity, Doppler curve
- [ ] Synthesizers: thunder rumble from filtered noise bursts; prop tone with harmonics and Doppler; mixer with background noise at a set SNR plus optional AAC/MP3 encode and high-pass to mimic real files
- [ ] `earwitness simulate` writes a WAV plus `truth.json` (location, start time, events used)
- [ ] Register the PyPI name by publishing `earwitness` 0.0.1 through trusted publishing once `simulate` works. A pending publisher alone does not reserve the name, and an empty placeholder counts as name squatting under PEP 541, so the first release must do something real.

Done when: the worked Doppler example (130 Hz at -57.97 m/s gives 156.44 Hz) passes; the emission-time solver converges within 5 iterations; the wrong (reception-time) model produces a measurably different curve shape.

### M2: Thunder pipeline on GLM

- [ ] GLM downloader for a date and bbox, cached as Parquet
- [ ] Observed envelope, coarse grid with strike-count prefilter and land mask, FFT sliding cosine, refinement, outputs
- [ ] Bearing-diversity weighting option for clustered storms

Done when: on 10 synthetic cases built from real GLM storm days (at least 10 strikes in 30 minutes), the true location ranks first and within 5 km in at least 8; a one-day continental-US search finishes in under 30 minutes on an 8-core desktop.

### M3: Aircraft data

- [ ] adsb.lol day downloader (reassemble split tars), trace parser, region and time clip
- [ ] Type lookup and engine classification; 1 s interpolation; vertical datum handling with DEM
- [ ] Cached as per-day, per-region Parquet

Done when: one continental-US day processes end to end, and spot-checked flights match the adsb.lol replay.

### M4: Aircraft pipeline

- [ ] Event model and `events.json` schema (ranges for `f0`, `v`, `d`, `t0`; silence windows; optional traced curve)
- [ ] Funnel: speed gate, distance tube, timing pair, silence filter; single-event known-time mode
- [ ] Exact modeling, locked two-event scoring, 100 m refinement, grouping by flight pair

Done when: synthetic two-event cases recover truth within 200 m ranked first in at least 8 of 10; single-event known-time cases do so in at least 6 of 10.

### M5: Desktop UI

- [ ] FastAPI server, Vite front end, pywebview shell
- [ ] Views from section 6, including the Doppler fit and trace tools

Done when: a user can go from a file to a heat map for both methods without the CLI.

### M6: Real-world validation

- [ ] Run the protocol in section 8 on personal recordings and on colsto's published test recording
- [ ] Write results (error distance, rank, runtime) into `docs/VALIDATION.md`

Done when: at least one real aircraft case and one real thunder case are documented, including failures.

### M7: Release v0.1.0

- [ ] Windows build, install docs, data-source attribution, `COUNTERMEASURES.md`
- [ ] Project site at earwitness.dev (GitHub Pages with a custom domain): method, data sources, countermeasures, validation results. .dev domains are HTTPS-only, which GitHub Pages handles.
- [ ] Tagged release with known limitations listed

Done when: a clean Windows machine can install and run the synthetic demo.

## Testing and validation

Synthetic cases with known answers drive development; real recordings validate it at the end.

**Test layers.**

- **Unit tests** for every physics function, each with a worked number (Doppler example, slant range, emission time, cosine score on hand-built arrays).
- **Synthetic end to end.** `simulate` picks a location and start time, renders a recording from real GLM or ADS-B data for that day, and the search must find it. Run each case clean, with noise, after AAC encoding, and after a high-pass filter (video 3 showed the Doppler curve survived high-pass plus MP3 once gain was boosted).
- **Blind mode.** The harness hides `truth.json` from the search and scores the result afterward: error distance, rank of the true point, score margin over the runner-up, runtime.
- **Regression set.** A fixed set of synthetic cases checked in as small fixtures, rerun in CI.

**Real-world recording protocol.**

1. Record WAV at 48 kHz with a decent external mic, outdoors or near an open window, for at least 20 minutes (aircraft) or 30 minutes (thunder).
2. Capture the start time against an accurate UTC clock: start recording while an NTP-synced clock is on camera, or speak the time at the start. Single-event mode needs about 1 s accuracy; two-event mode does not need absolute time.
3. Log the true coordinates in a private file under `data/`, never in the repo.
4. For blind tests, strip metadata first. Phone videos often embed GPS coordinates and creation time.
5. Aircraft cases can be recorded any day near a general-aviation airport. Thunder cases need storm season. Until then, record public bird-feeder livestreams with audio when GLM shows lightning nearby (video 1's approach, using GLM instead of Blitzortung as the trigger), within each stream's terms.

**External reference case.** colsto published his video 3 test recording (security camera, filmed September 8, continental US, linked in that video's description). Reproducing his result is the best single check of the aircraft pipeline.

## Risks and gotchas

The biggest risks are data gaps and faint-signal detection, not compute.

| Risk | Effect | Mitigation |
| --- | --- | --- |
| ADS-B gaps: small planes are not required to broadcast in much low-altitude airspace, and community receivers miss low rural traffic | Heard planes with no track are unusable; missing tracks weaken the silence filter | Treat silence as a soft penalty, not a hard cut, when coverage is thin; show receiver coverage on the map |
| Barometric altitude in ADS-B is pressure altitude, not true height | Slant distance error of tens to hundreds of meters | Prefer geometric altitude; else correct with a nearby airport's altimeter setting |
| Vertical datums differ: GNSS ellipsoid height vs DEM orthometric height | Tens of meters of height error | Convert both to one datum with a geoid model in pyproj |
| GLM is optical and space-based | Flash centroids, in-cloud flashes, 70 to 90% detection | Model source height per flash type; tolerate missing and extra events in scoring |
| Clustered strikes (video 2) | Ridge of equal scores instead of a point | Bearing-diversity weighting; report the ambiguity shape, not just the top point |
| Faint Doppler curves | Hard to detect automatically | Manual fit and trace tools in the UI first; auto-detection later |
| Wind noise in 20 to 250 Hz | False thunder peaks | Detect and mask wind segments |
| Disk: adsb.lol is gigabytes per day | Slow first runs, full disks | Region clipping on ingest, cache size limits, delete raw tars after conversion |
| Compute in the aircraft funnel | Billions of point-flight pairs if done naively | Order filters by selectivity (speed, then distance tube, then timing); vectorize with numba |
| Recording clock drift and unknown sample rate errors | Small timing error over long clips | Negligible for 30 minutes at typical 50 ppm (about 90 ms), but log sample rate and duration |

## Responsible release

The project is framed as a privacy-awareness demonstration: show that recordings leak location, show how to check your own, and document what does and does not prevent it.

- colsto deliberately did not release code. An open-source version makes the technique runnable by anyone, so the framing decisions below are made up front, not after release.
- Lead with the audit mode in the README and UI: "does my recording contain location clues?"
- The default workflow and all examples use the user's own recordings or synthetic cases. Publish no results that locate third-party recordings.
- Ship `COUNTERMEASURES.md` from video 3's tests. What did not work: high-pass filtering (the curve came back after boosting gain), talking over it, phone call compression. What helps: cuts in the footage, which break the timing between events, and heavier noise reduction. Note that one event plus a known time was enough in video 3's 1-minute test.
- No hosted service, no media URL downloading, no bundled data.
- Blitzortung's policy asks operators to prevent misuse of their data. That is another reason it is not a default source.

## Open decisions for Chris

These need an answer before or during M0; the recommendation is the default if nothing is decided.

| Decision | Options | Recommendation |
| --- | --- | --- |
| Code license | MIT, Apache-2.0, GPLv3, AGPLv3 | Apache-2.0 (permissive, with a patent grant). GPL or AGPL if derivatives must stay open. |
| Global search in the UI | Ship it, CLI only, or region required | CLI only for global; UI requires a region or date prior |
| Platforms in CI | Windows only, Windows plus Linux | Windows plus Linux (cheap now, painful later) |
| UI stack | Local web UI in pywebview, PySide6 (Qt) | Local web UI |
| Optional Blitzortung adapter for station operators | Include, exclude | Exclude from v0.1 |
| Helicopters as Doppler sources | Include, exclude | Exclude from v0.1; rotor harmonics need their own model |

## Starting the project

Commit this doc as `docs/HANDOFF.md`, add the `CONTRIBUTING.md` below, then start on the first task.

**Starter `CONTRIBUTING.md`.**

```markdown
# Earwitness

Open-source PoC that geolocates recordings from background thunder and prop-aircraft Doppler sound.
Site: earwitness.dev. Python package and CLI: earwitness.
The full spec is docs/HANDOFF.md. Read it before changing anything in src/.

## Conventions
- Python 3.12, uv, ruff, pytest. Type hints on all public functions.
- SI units internally (meters, seconds, m/s, Hz). UTC everywhere. Convert only at I/O edges.
- Every physics function gets a unit test with a worked number.
- Engine and CLI first; the UI only calls the engine.
- Never commit anything under data/. No third-party datasets in the repo.
- Never use Blitzortung or LightningMaps endpoints.
- Ask before adding a dependency not listed in docs/HANDOFF.md.
- Work milestone by milestone (HANDOFF section 7). Stop at each milestone's acceptance check and report.
```

**First task.**

```
Read docs/HANDOFF.md end to end. Then implement M0 and M1 from section 7.
Start with core/geodesy.py and core/physics.py and their tests, including the
worked Doppler example and the emission-time vs reception-time shape test.
Then build simulate/ so `earwitness simulate aircraft` and `earwitness simulate thunder`
write a WAV plus truth.json from synthetic tracks and strikes (real data loaders
come in M2 and M3). Stop when M1's acceptance check passes and summarize what
you built, what you assumed, and anything in the spec that looked wrong.
```

## Sources

- colsto: [Can you geolocate a video using background thunder sounds?](https://www.youtube.com/watch?v=SnbkIm56TfI), [I located a fan's video using thunder sounds](https://www.youtube.com/watch?v=6a7Z0I3LAnE), [I discovered a sound that leaks your exact location](https://www.youtube.com/watch?v=UytYrhYLOKI)
- [NOAA GOES on AWS](https://registry.opendata.aws/noaa-goes/) and [GOES-19 GLM validation review, Mar 2025](https://noaasis.noaa.gov/pdf/ps-pvr/goes19/GLM/Provisional/GLM19_PROV_PS-PVR_13Mar2025.pdf)
- [adsb.lol historical data](https://www.adsb.lol/docs/open-data/historical/) and [Bellingcat Turnstone](https://github.com/bellingcat/adsb-history)
- [OpenSky Trino access policy](https://openskynetwork.github.io/opensky-api/trino.html)
- [MET Norway API](https://api.met.no) and [Frost](https://frost.met.no)
- [Blitzortung data policy thread](https://forum.blitzortung.org/showthread.php?pid=17453) and [LightningMaps about page](https://www.lightningmaps.org/about)
- [PyPI: creating a project with a pending trusted publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) and [PEP 541 invalid projects](https://peps.python.org/pep-0541/#invalid-projects)
