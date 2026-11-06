# Contributing and conventions

Earwitness is an open-source PoC that geolocates recordings from background thunder and
prop-aircraft Doppler sound. Site: earwitness.dev. Python package and CLI: earwitness.
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

## Working notes
- Commands: `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`,
  `uv run earwitness --help`, `uv run earwitness demo`.
- The CLI uses argparse (stdlib) because the handoff lists no CLI library.
- ffmpeg is optional: needed only for `simulate ... --codec aac|mp3` and for decoding non-WAV
  input. Tests that need it skip when it is missing; CI installs it.
- Open decisions (HANDOFF "Open decisions for Chris") are settled as the recommendation column,
  confirmed on 2026-11-03: Apache-2.0, CI on Windows plus Linux, local web UI, no global search
  in the UI, no Blitzortung adapter, no helicopters in v0.1.
- Repo: https://github.com/cajdata/earwitness (public). Milestone state: M0 to M5 built; M6
  (real-world validation) met its criterion on 2026-11-05 (two aircraft cases located; one
  public thunder recording located, nine documented as failures); acceptance results are in
  docs/VALIDATION.md. M7 (release v0.1.0 and the site) built on 2026-11-05: the Windows build,
  docs/INSTALL.md, docs/COUNTERMEASURES.md, the attribution in docs/DATA_SOURCES.md, the
  release notes in docs/release-notes/, and the site; docs/RELEASING.md has the steps. v0.1.0
  released on 2026-11-06 (PyPI, and a GitHub Release with the Windows zip) after the
  clean-machine check passed in Windows Sandbox.
- `earwitness demo` is the install check: library checks on tiny files of their own (GDAL and
  PROJ, the GLM NetCDF reader, Parquet, GEOS), then the two synthetic cases located offline.
  Keep it offline and under a minute; the Windows build's smoke test and the clean-machine
  check both run it. `earwitness-ui` (a pip gui-script, and `earwitness-ui.exe`) is the
  console-less launcher in `earwitness.desktop`: it logs to `earwitness-ui.log` and, on
  Windows, reports in message boxes. Without WebView2, GTK or Qt the UI opens in the browser.
- Windows build: `packaging/` (PyInstaller spec, entry scripts, a rasterio hook,
  `build_windows.py`, and the Windows Sandbox clean-machine test in `packaging/sandbox/`).
  PyInstaller is in the `build` dependency group:
  `uv run --group build python packaging/build_windows.py --build-ui --smoke` writes
  `dist/windows/`. CI builds it in `.github/workflows/build-windows.yml` (manual or on a tag).
- Site: `site/` is plain HTML and one CSS file, no build step, no scripts, nothing loaded from
  other servers; `.github/workflows/pages.yml` deploys it to earwitness.dev after
  `tests/test_site.py` (links, anchors, self-containment, no coordinates) passes. Main pages
  use short plain copy; formulas and run numbers go on `in-depth.html`. Never put the place of
  a real recording, or anything that points at one, on the site.
- `scripts/m7_countermeasures.py` edits the demo cases (low-cut filters, AAC and MP3, cuts,
  clocks) and searches them: the table in docs/COUNTERMEASURES.md.
- UI: `ui/` is a Vite plus TypeScript front end (MapLibre GL JS, Plotly.js) with no framework.
  `cd ui && npm install && npm run build` writes it into `src/earwitness/server/static/`
  (gitignored, shipped in the wheel through hatch `artifacts`); CI and the release build it
  with Node 22. For live front-end work run `uv run earwitness ui --serve --port 8765` and
  `npm run dev` in `ui/`, which proxies `/api` to it. The server is FastAPI on uvicorn (the ASGI
  server FastAPI needs, the one runtime addition to the handoff's stack) and the window is
  pywebview; the test client needs httpx (dev only).
- Data cache: `data/cache` (gitignored) or `EARWITNESS_CACHE`; GLM hours are a few MB each as
  Parquet. `scripts/m2_acceptance.py` rebuilds the M2 validation from real GLM data and
  `scripts/m4_acceptance.py` the M4 validation from a cached adsb.lol day (about ten minutes
  for single-event mode, an hour or more for two-event mode).
- M6 real cases: the truth (lat, lon, optional start_utc, notes) lives in a private JSON under
  `data/`, such as `data/m6/<case>/truth.json`. Run the analysis blind with the CLI or the UI,
  then `uv run python scripts/m6_score.py --run <run> --truth <truth.json>` prints the row for
  docs/VALIDATION.md. `scripts/m6_storm_watch.py` shows where GLM sees lightning right now.
- Aircraft events may carry a `valley` (rows `[v_mps, d_m, f0_hz, t0_s]` from the fit's speed
  profile, see `earwitness.aircraft.fit.profile_speed`): a trace without its wings fixes only
  v^2 / d, and the funnel and scorer narrow distance, fundamental and timing to each flight's
  own speed with it. `analyze aircraft --max-pairs` raises the prefilter's hint cap. Before a
  large-region run, benchmark hints and candidates per hint on the flight set first, and run
  it in the background with its own log; its `run.log` reports prefilter and funnel progress.
  A known recording time (`--known-time`, `--known-time-tolerance`) constrains a two-event
  search as well as driving the single-event mode, and limits the flights to the clip's window
  first: use every clue the recording gives. `--workers N` scores candidates in N processes.
- Real-case tools in `scripts/`: `m6_spectrogram.py` (overview strips and zooms, the first look),
  `m6_measure.py` (trace and fit flyovers the UI's way into events.json, with the speed
  profile and heard times), `m6_event_overlay.py` (trace and fits over the spectrogram),
  `m6_truth_check.py` (when a case fails: which flights passed the true site and how it scores
  by hand with them; every M6 bug was a measurement or convention problem it exposed),
  `m6_clip_sweep.py` (cut excerpts around the flyovers and run known-time searches on them:
  how short a clip can be, with the analyst's trace cut to each excerpt or re-traced),
  `m6_thunder_shortlist.py` (public radio aporee recordings of thunder on the Internet Archive
  with a published position inside GLM coverage and an open licence, rated from sampled GLM
  files by how sparse and how near the storm was at the stated time; its output carries
  positions, so it goes under `data/`).
