"""`earwitness demo`: two synthetic recordings with known answers, located offline.

The demo is the release's install check (HANDOFF M7: a clean Windows machine installs and runs
the synthetic demo). It renders a thunder recording and a two-flyover aircraft recording with
the simulator, audits each, then searches each against the synthetic strikes and tracks the
simulator wrote beside it, and checks the answers against the truth with the acceptance widths
of M2 (5 km) and M4 (200 m). It needs no network, no data cache and no ffmpeg: the land mask
and terrain are off, as they must be for synthetic cases rendered on flat ground.

The cases are the well-conditioned ones of the end-to-end tests (two storm cells in different
directions; two flyovers, a far decoy and a decoy crossing the site while the recording is
silent), with fixed seeds, so every run gives the same answer.

Before the cases, `install_checks` runs each library the demo itself never touches on a tiny
file of its own (a GeoTIFF through GDAL and PROJ, a GLM-like NetCDF file through the real GLM
reader, a Parquet table, a land polygon) and reports the desktop window's engine and ffmpeg,
so a broken install or a frozen build missing a module shows up here and not halfway through a
real analysis.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

DEMO_LAT, DEMO_LON = 35.0, -97.0
"""The synthetic site: open country, chosen for the end-to-end tests, not a real recording."""

THUNDER_START = datetime(2026, 7, 2, 21, 0, tzinfo=UTC)
AIRCRAFT_START = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
THUNDER_TOLERANCE_M = 5000.0
AIRCRAFT_TOLERANCE_M = 200.0
SAMPLE_RATE_HZ = 4000

Say = Callable[[str], None]


@dataclass
class DemoResult:
    """One located synthetic case."""

    method: str
    error_m: float
    """Distance of the rank-1 answer from the true site."""
    start_error_s: float
    """Rank-1 recording start minus the true start."""
    tolerance_m: float
    rank_within: int | None
    """First rank within the tolerance, or None."""
    score: float
    runtime_s: float
    case_dir: Path
    run_dir: Path

    @property
    def passed(self) -> bool:
        """True when the rank-1 answer lies within the tolerance."""
        return self.rank_within == 1


@dataclass
class Check:
    """One install check: `status` is ok, fail, or info for optional parts."""

    label: str
    status: str
    detail: str = ""


def _check_terrain() -> str:
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin

    grid = np.arange(16, dtype=np.float32).reshape(1, 4, 4)
    profile = {"driver": "GTiff", "width": 4, "height": 4, "count": 1, "dtype": "float32"}
    with MemoryFile() as mem:
        transform = from_origin(-97.0, 35.0, 0.25, 0.25)
        with mem.open(**profile, crs="EPSG:4326", transform=transform) as ds:  # PROJ's database
            ds.write(grid)
        with mem.open() as ds:
            if float(ds.read(1)[1, 2]) != 6.0 or ds.crs.to_epsg() != 4326:
                raise RuntimeError("a GeoTIFF did not read back")
    return f"GDAL {rasterio.__gdal_version__}"


def _check_lightning_files() -> str:
    import xarray as xr

    from earwitness.data.glm import read_glm_file

    ds = xr.Dataset(
        {
            "flash_lat": ("number_of_flashes", np.array([35.0, 35.1], dtype=np.float32)),
            "flash_lon": ("number_of_flashes", np.array([-97.0, -97.1], dtype=np.float32)),
            "flash_time_offset_of_first_event": (
                "number_of_flashes",
                np.array([1.0, 2.5], dtype=np.float32),
                {"units": "seconds since 2025-05-16 23:00:00.000"},
            ),
            "flash_energy": ("number_of_flashes", np.full(2, 1e-14, dtype=np.float32)),
            "flash_area": ("number_of_flashes", np.full(2, 2.5e8, dtype=np.float32)),
            "flash_quality_flag": ("number_of_flashes", np.zeros(2, dtype=np.float32)),
            "flash_id": ("number_of_flashes", np.arange(2, dtype=np.uint16)),
        },
        attrs={"platform_ID": "G19"},
    )
    with tempfile.TemporaryDirectory(prefix="earwitness-check-") as tmp:
        path = Path(tmp) / "glm-check.nc"
        ds.to_netcdf(path, engine="h5netcdf")
        batch = read_glm_file(path)
    if batch.lat.size != 2 or batch.satellite != "G19":
        raise RuntimeError("a GLM-like NetCDF file did not read back")
    return "the GLM reader on a NetCDF file"


def _check_tables() -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq

    sink = pa.BufferOutputStream()
    pq.write_table(pa.table({"x": [1, 2, 3]}), sink, compression="zstd")
    if pq.read_table(pa.BufferReader(sink.getvalue())).column("x").to_pylist() != [1, 2, 3]:
        raise RuntimeError("a Parquet table did not read back")
    return f"pyarrow {pa.__version__}, zstd"


def _check_land_mask() -> str:
    import shapely

    if abs(shapely.Point(0.0, 0.0).buffer(1.0, quad_segs=64).area - np.pi) > 1e-3:
        raise RuntimeError("a buffered point has the wrong area")
    return f"GEOS {shapely.geos_version_string}"


def install_checks() -> list[Check]:
    """Each library the analyses load lazily, on a small file of its own; then the optional
    parts: the desktop window's engine and ffmpeg."""
    checks: list[Check] = []
    for label, func in (
        ("terrain tiles (rasterio)", _check_terrain),
        ("lightning files (h5netcdf)", _check_lightning_files),
        ("data cache (Parquet)", _check_tables),
        ("land mask (shapely)", _check_land_mask),
    ):
        try:
            checks.append(Check(label, "ok", func()))
        except Exception as exc:
            checks.append(Check(label, "fail", f"{type(exc).__name__}: {exc}"))
    from earwitness.server.launch import webview2_version, window_unavailable

    reason = window_unavailable()
    if reason is None:
        engine = webview2_version()
        detail = f"Microsoft Edge WebView2 {engine}" if engine else "pywebview"
        checks.append(Check("desktop window", "ok", detail))
    else:
        checks.append(Check("desktop window", "info", f"{reason}: the UI opens in your browser"))
    from earwitness.audio.decode import ffmpeg_path

    ffmpeg = ffmpeg_path()
    if ffmpeg:
        checks.append(Check("video and other audio", "ok", f"ffmpeg at {ffmpeg}"))
    else:
        checks.append(
            Check("video and other audio", "info", "no ffmpeg on PATH: WAV, FLAC and OGG only")
        )
    return checks


def check_line(c: Check) -> str:
    """`install   terrain tiles (rasterio)      ok    GDAL 3.11` as the demo prints it."""
    mark = {"ok": "ok", "fail": "FAIL", "info": "--"}[c.status]
    return f"install   {c.label:<28} {mark:<5} {c.detail}"


def _fresh_dir(parent: Path, name: str) -> Path:
    """`parent/name`, or `parent/name-2` and so on when it exists, like the UI's run folders."""
    out = parent / name
    n = 1
    while out.exists():
        n += 1
        out = parent / f"{name}-{n}"
    return out


def _distance_m(lat: float, lon: float) -> float:
    from earwitness.core import geodesy

    return float(geodesy.horizontal_distance(lat, lon, DEMO_LAT, DEMO_LON))


def _first_within(summary: dict[str, object], tolerance_m: float) -> int | None:
    for t in summary["top"]:  # type: ignore[union-attr]
        if _distance_m(t["lat"], t["lon"]) <= tolerance_m:  # type: ignore[index]
            return int(t["rank"])  # type: ignore[index]
    return None


def write_thunder_demo_case(case_dir: Path, rate: int = SAMPLE_RATE_HZ) -> dict[str, object]:
    """A 15 minute recording of two storm cells 9 and 12 km away in different directions."""
    from earwitness.simulate import MixOptions, write_thunder_case
    from earwitness.simulate.thunder_synth import ThunderScenario, random_storm

    seed = 11
    rng = np.random.default_rng(seed)
    strikes = []
    for bearing, distance_m, n in ((50.0, 9000.0, 18), (200.0, 12000.0, 18)):
        strikes += random_storm(
            rng,
            DEMO_LAT,
            DEMO_LON,
            900.0,
            n,
            center_bearing_deg=bearing,
            center_distance_m=distance_m,
            radius_m=4000.0,
            drift_speed_mps=8.0,
            drift_bearing_deg=bearing + 60.0,
        )
    strikes.sort(key=lambda s: s.time_s)
    scenario = ThunderScenario(DEMO_LAT, DEMO_LON, 0.0, THUNDER_START, 900.0, strikes)
    return write_thunder_case(case_dir, scenario, rate, MixOptions(snr_db=10.0), seed, rng)


def write_aircraft_demo_case(case_dir: Path, rate: int = SAMPLE_RATE_HZ) -> dict[str, object]:
    """A 15 minute recording with two light aircraft passing 4 minutes apart."""
    from earwitness.simulate import MixOptions, write_aircraft_case
    from earwitness.simulate.aircraft_synth import AircraftScenario, Flyover

    seed = 21
    flyovers = [
        Flyover(70.0, 400.0, 90.0, 300.0, t_cpa_s=300.0, f0_hz=110.0, harmonics=6, icao="ad5b01"),
        Flyover(85.0, 500.0, 200.0, -250.0, t_cpa_s=540.0, f0_hz=95.0, harmonics=6, icao="ad5b02"),
    ]
    scenario = AircraftScenario(
        DEMO_LAT, DEMO_LON, 0.0, AIRCRAFT_START, 900.0, flyovers, track_half_span_s=400.0
    )
    return write_aircraft_case(
        case_dir,
        scenario,
        rate,
        MixOptions(snr_db=-15.0),
        seed,
        np.random.default_rng(seed),
    )


def _audit_line(recording: Path) -> str:
    from earwitness.audit.report import audit_file

    report = audit_file(recording)
    found = [f"{c.label} {c.confidence}" for c in report.clues if c.present]
    return "audit finds " + (", ".join(found) if found else "no clue")


def run_thunder_demo(
    out_dir: Path, runs_dir: Path, say: Say, progress: Say | None = None
) -> DemoResult:
    """Simulate, audit and locate the thunder case."""
    from earwitness.core.grid import BBox
    from earwitness.data.adapters import load_events
    from earwitness.thunder.pipeline import PipelineConfig, ThunderPriors, analyze_thunder

    case_dir = out_dir / "thunder"
    say("thunder   simulating 15 minutes of two storm cells 9 and 12 km away")
    write_thunder_demo_case(case_dir)
    recording = case_dir / "recording.wav"
    say(f"thunder   {_audit_line(recording)}")
    events = load_events(case_dir / "strikes.json")
    priors = ThunderPriors(
        THUNDER_START - timedelta(minutes=30),
        THUNDER_START + timedelta(minutes=30),
        BBox.around(DEMO_LAT, DEMO_LON, 60000.0),
    )
    say(
        f"thunder   searching a 120 by 120 km region and an hour of start times "
        f"against {len(events)} synthetic strikes"
    )
    run_dir = _fresh_dir(runs_dir, "demo-thunder")
    cfg = PipelineConfig(top_k=5, use_land_mask=False, overlay_count=2)
    t0 = time.perf_counter()
    summary = analyze_thunder(recording, events, priors, run_dir, cfg, None, progress)
    best = summary["top"][0]  # type: ignore[index]
    return DemoResult(
        method="thunder",
        error_m=_distance_m(best["lat"], best["lon"]),
        start_error_s=float(best["offset_unix"]) - THUNDER_START.timestamp(),
        tolerance_m=THUNDER_TOLERANCE_M,
        rank_within=_first_within(summary, THUNDER_TOLERANCE_M),
        score=float(best["score"]),
        runtime_s=time.perf_counter() - t0,
        case_dir=case_dir,
        run_dir=run_dir,
    )


def with_decoys(tracks_json: Path) -> list[object]:
    """The case's two flights plus two decoys: the same tracks moved 9 km north and 33 minutes
    later, and moved 550 m south and 25 minutes earlier, so the second decoy crosses the site
    while the recording is silent and the silence filter has to reject it."""
    from earwitness.aircraft.prep import flights_from_tracks_json

    flights = flights_from_tracks_json(tracks_json)
    decoys = flights_from_tracks_json(tracks_json)
    for d, (dt_s, dlat) in zip(decoys, [(2000.0, 0.08), (-1500.0, -0.005)], strict=True):
        d.icao = "decoy" + d.icao[-1]
        d.time_unix = d.time_unix + dt_s
        d.lat = d.lat + dlat
    return [*flights, *decoys]


def run_aircraft_demo(
    out_dir: Path, runs_dir: Path, say: Say, progress: Say | None = None, workers: int = 1
) -> DemoResult:
    """Simulate, audit and locate the aircraft case."""
    from earwitness.aircraft.pipeline import (
        AircraftPipelineConfig,
        AircraftPriors,
        analyze_aircraft,
    )
    from earwitness.audio.events import events_from_truth
    from earwitness.core.grid import BBox

    case_dir = out_dir / "aircraft"
    say("aircraft  simulating 15 minutes with two light aircraft passing 4 minutes apart")
    truth = write_aircraft_demo_case(case_dir)
    recording = case_dir / "recording.wav"
    say(f"aircraft  {_audit_line(recording)}")
    events = events_from_truth(truth)
    events.save(case_dir / "events.json")
    flights = with_decoys(case_dir / "tracks.json")
    priors = AircraftPriors(
        AIRCRAFT_START - timedelta(minutes=10),
        AIRCRAFT_START + timedelta(minutes=10),
        BBox.around(DEMO_LAT, DEMO_LON, 15000.0),
    )
    say(
        f"aircraft  searching a 30 by 30 km region and 20 minutes of start times against "
        f"{len(flights)} synthetic flights, two of them decoys"
    )
    run_dir = _fresh_dir(runs_dir, "demo-aircraft")
    # The case has a few dozen candidates, far under the pool's usual threshold, so with more
    # than one worker the demo sends every chunk of 8 to the pool: that is the point of asking
    # for workers here, a check that the install can score in child processes.
    cfg = AircraftPipelineConfig(
        use_terrain=False,
        top_pairs=5,
        heatmap_pairs=1,
        workers=workers,
        parallel_min_rows=0 if workers > 1 else AircraftPipelineConfig.parallel_min_rows,
        parallel_chunk_rows=8,
    )
    t0 = time.perf_counter()
    summary = analyze_aircraft(
        recording,
        events,
        flights,  # type: ignore[arg-type]
        priors,
        run_dir,
        cfg,
        None,
        progress,
    )
    best = summary["top"][0]  # type: ignore[index]
    return DemoResult(
        method="aircraft",
        error_m=_distance_m(best["lat"], best["lon"]),
        start_error_s=float(best["t_start_unix"]) - AIRCRAFT_START.timestamp(),
        tolerance_m=AIRCRAFT_TOLERANCE_M,
        rank_within=_first_within(summary, AIRCRAFT_TOLERANCE_M),
        score=float(best["score"]),
        runtime_s=time.perf_counter() - t0,
        case_dir=case_dir,
        run_dir=run_dir,
    )


def result_line(r: DemoResult) -> str:
    """`thunder   rank 1 is 0.3 km from the truth ... PASS` as the demo prints it."""
    where = f"{r.error_m / 1000.0:.2f} km" if r.error_m >= 1000.0 else f"{r.error_m:.0f} m"
    limit = f"{r.tolerance_m / 1000.0:g} km" if r.tolerance_m >= 1000.0 else f"{r.tolerance_m:g} m"
    verdict = "PASS" if r.passed else "FAIL"
    return (
        f"{r.method:<9} rank 1 is {where} from the true site, start {r.start_error_s:+.1f} s; "
        f"{verdict} (needs rank 1 within {limit}), {r.runtime_s:.0f} s"
    )


def run_demo(
    out_dir: str | Path,
    runs_dir: str | Path,
    say: Say = print,
    progress: Say | None = None,
    workers: int = 1,
) -> tuple[list[Check], list[DemoResult]]:
    """Run the install checks and both cases; `say` gets the demo's own lines, `progress` the
    pipelines' logs."""
    out, runs = Path(out_dir), Path(runs_dir)
    out.mkdir(parents=True, exist_ok=True)
    runs.mkdir(parents=True, exist_ok=True)
    checks = install_checks()
    for c in checks:
        say(check_line(c))
    results = [run_thunder_demo(out, runs, say, progress)]
    say(result_line(results[-1]))
    results.append(run_aircraft_demo(out, runs, say, progress, workers))
    say(result_line(results[-1]))
    return checks, results
