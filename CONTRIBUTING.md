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
  `uv run earwitness --help`.
- The CLI uses argparse (stdlib) because the handoff lists no CLI library.
- ffmpeg is optional: needed only for `simulate ... --codec aac|mp3` and for decoding non-WAV
  input. Tests that need it skip when it is missing; CI installs it.
- Open decisions (HANDOFF "Open decisions for Chris") are settled as the recommendation column,
  confirmed on 2026-11-03: Apache-2.0, CI on Windows plus Linux, local web UI, no global search
  in the UI, no Blitzortung adapter, no helicopters in v0.1.
- Repo: https://github.com/cajdata/earwitness (public). Milestone state: M0 to M3 built; acceptance
  results are in docs/VALIDATION.md. Next is M4 (aircraft funnel and scoring).
- Data cache: `data/cache` (gitignored) or `EARWITNESS_CACHE`; GLM hours are a few MB each as
  Parquet. `scripts/m2_acceptance.py` rebuilds the M2 validation from real GLM data.
