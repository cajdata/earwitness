"""End to end: synthetic two-flyover and single-flyover recordings must be located."""

import json
from datetime import UTC, datetime, timedelta

import numpy as np

from earwitness import cli
from earwitness.aircraft import model as am
from earwitness.aircraft.funnel import FunnelConfig
from earwitness.aircraft.pipeline import (
    AircraftPipelineConfig,
    AircraftPriors,
    analyze_aircraft,
    distance_to,
)
from earwitness.aircraft.prep import flights_from_tracks_json
from earwitness.aircraft.score import SpectrogramIndex
from earwitness.audio.events import events_from_truth
from earwitness.audio.spectrogram import Spectrogram
from earwitness.core.grid import BBox
from earwitness.simulate import MixOptions, write_aircraft_case
from earwitness.simulate.aircraft_synth import AircraftScenario, Flyover

START = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
LAT, LON = 35.0, -97.0


def _case(tmp_path, seed=21):
    flyovers = [
        Flyover(70.0, 400.0, 90.0, 300.0, t_cpa_s=300.0, f0_hz=110.0, harmonics=6, icao="ad5b01"),
        Flyover(85.0, 500.0, 200.0, -250.0, t_cpa_s=540.0, f0_hz=95.0, harmonics=6, icao="ad5b02"),
    ]
    scenario = AircraftScenario(LAT, LON, 0.0, START, 900.0, flyovers, track_half_span_s=400.0)
    case = tmp_path / "case"
    truth = write_aircraft_case(
        case, scenario, 4000, MixOptions(snr_db=-15.0), seed, np.random.default_rng(seed)
    )
    flights = flights_from_tracks_json(case / "tracks.json")
    # decoys: one far away, one crossing the site during the silence window
    decoys = flights_from_tracks_json(case / "tracks.json")
    for d, (dt, dlat) in zip(decoys, [(2000.0, 0.08), (-1500.0, -0.005)], strict=True):
        d.icao = "decoy" + d.icao[-1]
        d.time_unix = d.time_unix + dt
        d.lat = d.lat + dlat
    return case, truth, flights + decoys


def test_spectrogram_index_brightness():
    spec = Spectrogram(
        t=np.array([0.0, 0.5, 1.0]),
        f=np.array([0.0, 10.0, 20.0, 30.0]),
        power=np.ones((4, 3)),
        whitened_db=np.array(
            [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [20.0, 0.0, 0.0], [30.0, 0.0, 0.0]]
        ),
    )
    index = SpectrogramIndex(spec)
    np.testing.assert_allclose(
        index.brightness(np.array([0.0, 0.0, 0.0]), np.array([10.0, 15.0, 30.0])),
        [10.0, 15.0, 30.0],
    )
    assert np.isnan(index.brightness(np.array([0.0]), np.array([35.0]))[0])
    assert index.brightness(np.array([0.6]), np.array([10.0]))[0] == 0.0  # nearest frame is 0.5
    assert index.frames_between(0.2, 0.9).tolist() == [0.5]


def test_two_event_pipeline_recovers_the_site(tmp_path):
    case, truth, flights = _case(tmp_path)
    events = events_from_truth(truth)
    assert len(events.events) == 2
    assert events.silence
    priors = AircraftPriors(
        START - timedelta(minutes=10), START + timedelta(minutes=10), BBox.around(LAT, LON, 15000.0)
    )
    cfg = AircraftPipelineConfig(use_terrain=False, top_pairs=5, heatmap_pairs=1)
    run = tmp_path / "run"
    summary = analyze_aircraft(case / "recording.wav", events, flights, priors, run, cfg)
    top = summary["top"]
    assert top
    assert top[0]["rank"] == 1
    assert distance_to(summary, LAT, LON)[0] < 200.0
    assert abs(top[0]["t_start_unix"] - START.timestamp()) < 2.0
    assert [f["icao"] for f in top[0]["flights"]] == ["ad5b01", "ad5b02"]
    assert summary["counts"]["candidates"] > 0
    for name in ("results.parquet", "heatmap.geojson", "summary.json", "config.toml", "run.log"):
        assert (run / name).exists(), name
    overlay = json.loads((run / "overlays" / "rank_01.json").read_text())
    assert len(overlay["curves"]) == 2
    assert overlay["curves"][0]["modeled_hz"]


def test_soft_silence_mode_penalizes_instead_of_dropping(tmp_path):
    case, truth, flights = _case(tmp_path, seed=24)
    events = events_from_truth(truth)
    priors = AircraftPriors(
        START - timedelta(minutes=10), START + timedelta(minutes=10), BBox.around(LAT, LON, 15000.0)
    )
    cfg = AircraftPipelineConfig(
        funnel=FunnelConfig(silence_mode="soft", silence_penalty_db=3.0),
        use_terrain=False,
        top_pairs=5,
        heatmap_pairs=1,
    )
    summary = analyze_aircraft(
        case / "recording.wav", events, flights, priors, tmp_path / "soft", cfg
    )
    counts = summary["counts"]
    assert counts["after_silence"] == counts["candidates"]  # nothing dropped
    top = summary["top"]
    assert distance_to(summary, LAT, LON)[0] < 200.0
    assert top[0]["silence_penalty_db"] == 0.0
    assert all("silence_penalty_db" in t for t in top)
    assert all(
        t["score"] <= t["coarse_score"] + 1e-9 or t["silence_penalty_db"] == 0.0 for t in top
    )


def test_single_event_known_time_recovers_the_site(tmp_path):
    case, truth, flights = _case(tmp_path, seed=22)
    events = events_from_truth(truth, max_events=1)
    events.known_time = START
    events.known_time_tolerance_s = 1.0
    priors = AircraftPriors(
        START - timedelta(minutes=10), START + timedelta(minutes=10), BBox.around(LAT, LON, 15000.0)
    )
    cfg = AircraftPipelineConfig(use_terrain=False, top_pairs=5, heatmap_pairs=1)
    summary = analyze_aircraft(
        case / "recording.wav", events, flights, priors, tmp_path / "run2", cfg
    )
    assert summary["mode"].startswith("single-event")
    # A single straight flyover is mirror-symmetric across the flight path: the truth and its
    # reflection score the same, so rank 1 may be either. Both must be found within 200 m.
    from earwitness.core import geodesy

    track = flights[0].track(LAT, LON, 0.0)
    _, slant = am.closest_approach(track)
    i = int(np.argmin(np.linalg.norm(track.enu, axis=1)))
    foot = track.enu[i].copy()
    foot[2] = 0.0
    mirror_lat, mirror_lon, _ = geodesy.enu_to_geodetic(2.0 * foot, LAT, LON, 0.0)
    best = summary["top"][0]
    d_truth = distance_to(summary, LAT, LON)[0]
    d_mirror = float(geodesy.horizontal_distance(best["lat"], best["lon"], mirror_lat, mirror_lon))
    assert min(d_truth, d_mirror) < 200.0
    assert slant > 400.0  # sanity: the flyover was not directly overhead
    assert summary["top"][0]["flights"][0]["icao"] == "ad5b01"


def test_cli_analyze_aircraft_with_tracks_file(tmp_path, capsys):
    case, truth, _ = _case(tmp_path, seed=23)
    events = events_from_truth(truth)
    events.save(case / "events.json")
    box = BBox.around(LAT, LON, 15000.0)
    run = tmp_path / "run3"
    args = [
        "analyze", "aircraft", str(case / "recording.wav"), "--events", str(case / "events.json"),
        "--date", "2026-09-08", f"--bbox={box}", "--tracks", str(case / "tracks.json"),
        "--no-terrain", "--top", "3", "--out", str(run), "--quiet",
    ]  # fmt: skip
    assert cli.main(args) == 0
    assert "run folder" in capsys.readouterr().out
    summary = json.loads((run / "summary.json").read_text())
    assert distance_to(summary, LAT, LON)[0] < 200.0
