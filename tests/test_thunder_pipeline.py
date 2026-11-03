"""End to end: a synthetic storm recording must be located by the thunder pipeline."""

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pyarrow.parquet as pq

from earwitness import cli
from earwitness.core import geodesy
from earwitness.core.grid import BBox
from earwitness.data.adapters import load_events
from earwitness.project import toml_dumps
from earwitness.simulate import MixOptions, write_thunder_case
from earwitness.simulate.thunder_synth import ThunderScenario, random_storm
from earwitness.thunder.pipeline import PipelineConfig, ThunderPriors, analyze_thunder, distance_to

START = datetime(2026, 7, 2, 21, 0, tzinfo=UTC)
LAT, LON = 35.0, -97.0
TWO_CELLS = [(50.0, 9000.0, 18), (200.0, 12000.0, 18)]
ONE_CELL = [(50.0, 9000.0, 30)]
OUTPUT_FILES = (
    "results.parquet",
    "heatmap.geojson",
    "histogram.json",
    "summary.json",
    "config.toml",
    "run.log",
    "priors.json",
)


def _make_case(tmp_path, seed, cells):
    """Storm cells as (bearing, distance, strikes). Two cells in different directions make a
    well-conditioned case; one cell leaves a ridge where range trades against start time."""
    rng = np.random.default_rng(seed)
    strikes = []
    for bearing, distance, n in cells:
        strikes += random_storm(
            rng,
            LAT,
            LON,
            900.0,
            n,
            center_bearing_deg=bearing,
            center_distance_m=distance,
            radius_m=4000.0,
            drift_speed_mps=8.0,
            drift_bearing_deg=bearing + 60.0,
        )
    strikes.sort(key=lambda s: s.time_s)
    scenario = ThunderScenario(LAT, LON, 0.0, START, 900.0, strikes)
    case = tmp_path / "case"
    write_thunder_case(case, scenario, 4000, MixOptions(snr_db=10.0), seed, rng)
    return case


def test_pipeline_recovers_a_well_conditioned_truth(tmp_path):
    case = _make_case(tmp_path, 11, TWO_CELLS)
    events = load_events(case / "strikes.json")
    priors = ThunderPriors(
        START - timedelta(minutes=30),
        START + timedelta(minutes=30),
        BBox.around(LAT, LON, 60000.0),
    )
    cfg = PipelineConfig(top_k=5, use_land_mask=False, overlay_count=2)
    run = tmp_path / "run"
    summary = analyze_thunder(case / "recording.wav", events, priors, run, cfg, landmask=None)

    top = summary["top"]
    assert top[0]["rank"] == 1
    assert distance_to(summary, LAT, LON)[0] < 500.0
    assert abs(top[0]["offset_unix"] - START.timestamp()) <= 2.0
    assert top[0]["score"] > 0.8
    assert summary["counts"]["grid"] > summary["counts"]["prefilter"] > 0
    assert summary["min_strikes"] >= 3

    for name in OUTPUT_FILES:
        assert (run / name).exists(), name
    assert (run / "overlays" / "rank_01.json").exists()
    assert (run / "overlays" / "rank_02.json").exists()

    table = pq.read_table(run / "results.parquet")
    stages = set(table.column("stage").to_pylist())
    assert {"coarse", "refine-1km", "refine-200m", "final"} <= stages
    heat = json.loads((run / "heatmap.geojson").read_text())
    finals = [f for f in heat["features"] if f["properties"]["stage"] == "final"]
    assert finals
    assert finals[0]["properties"]["rank"] == 1
    hist = json.loads((run / "histogram.json").read_text())
    assert sum(hist["counts"]) == summary["counts"]["prefilter"]
    overlay = json.loads((run / "overlays" / "rank_01.json").read_text())
    assert len(overlay["observed"]) == len(overlay["modeled"]) == len(overlay["t_s"])
    assert "[search]" in (run / "config.toml").read_text()


def test_single_cell_storm_lands_on_the_ridge_near_the_truth(tmp_path):
    # With every strike in one direction, range along that bearing trades against start time,
    # so the answer sits on a ridge through the truth: close, but not pinned down.
    case = _make_case(tmp_path, 12, ONE_CELL)
    events = load_events(case / "strikes.json")
    priors = ThunderPriors(
        START - timedelta(minutes=20),
        START + timedelta(minutes=20),
        BBox.around(LAT, LON, 40000.0),
    )
    cfg = PipelineConfig(top_k=3, use_land_mask=False)
    summary = analyze_thunder(case / "recording.wav", events, priors, tmp_path / "run2", cfg)
    assert distance_to(summary, LAT, LON)[0] < 3000.0
    assert abs(summary["top"][0]["offset_unix"] - START.timestamp()) <= 10.0


def test_pipeline_without_refinement_reports_coarse_regions(tmp_path):
    case = _make_case(tmp_path, 13, TWO_CELLS)
    events = load_events(case / "strikes.json")
    priors = ThunderPriors(
        START - timedelta(minutes=20),
        START + timedelta(minutes=20),
        BBox.around(LAT, LON, 40000.0),
    )
    cfg = PipelineConfig(top_k=3, use_land_mask=False, do_refine=False)
    summary = analyze_thunder(case / "recording.wav", events, priors, tmp_path / "run3", cfg)
    assert 1 <= len(summary["top"]) <= 3
    assert distance_to(summary, LAT, LON)[0] < 4000.0  # within a 5 km coarse cell


def test_cli_analyze_thunder_with_strikes_file(tmp_path, capsys):
    case = _make_case(tmp_path, 14, TWO_CELLS)
    run = tmp_path / "run4"
    box = BBox.around(LAT, LON, 50000.0)
    args = [
        "analyze", "thunder", str(case / "recording.wav"),
        "--date", "2026-07-02", "--time-window", "2026-07-02T20:40:00Z", "2026-07-02T21:20:00Z",
        f"--bbox={box}",
        "--strikes", str(case / "strikes.json"), "--out", str(run),
        "--no-land-mask", "--top", "3", "--quiet",
    ]  # fmt: skip
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "rank" in out
    assert "run folder" in out
    summary = json.loads((run / "summary.json").read_text())
    best = summary["top"][0]
    assert float(geodesy.horizontal_distance(best["lat"], best["lon"], LAT, LON)) < 1000.0


def test_cli_bbox_accepts_separate_tokens(tmp_path):
    # Negative longitudes as separate tokens are read as numbers, not options.
    case = _make_case(tmp_path, 15, TWO_CELLS)
    run = tmp_path / "run5"
    args = [
        "analyze", "thunder", str(case / "recording.wav"),
        "--date", "2026-07-02", "--time-window", "2026-07-02T20:50:00Z", "2026-07-02T21:10:00Z",
        "--bbox", "-97.4", "34.7", "-96.6", "35.3",
        "--strikes", str(case / "strikes.json"), "--out", str(run),
        "--no-land-mask", "--no-refine", "--top", "2", "--quiet",
    ]  # fmt: skip
    assert cli.main(args) == 0
    summary = json.loads((run / "summary.json").read_text())
    assert summary["region_used"] == [-97.4, 34.7, -96.6, 35.3]


def test_cli_analyze_thunder_needs_a_region_without_strikes(tmp_path, capsys):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"")
    assert cli.main(["analyze", "thunder", str(wav), "--date", "2026-07-02", "--quiet"]) == 2
    assert "needs --bbox" in capsys.readouterr().err
    missing = str(tmp_path / "missing.wav")
    assert cli.main(["analyze", "thunder", missing, "--date", "2026-07-02"]) == 2


def test_toml_dumps_is_simple_and_readable():
    text = toml_dumps(
        {
            "name": "run",
            "count": 3,
            "ratio": 0.5,
            "flag": True,
            "search": {"step_s": 5.0, "tags": ["a", "b"], "none": None},
        }
    )
    assert 'name = "run"' in text
    assert "[search]" in text
    assert "step_s = 5.0" in text
    assert 'tags = ["a", "b"]' in text
    assert "flag = true" in text
