"""End to end: simulated recordings must contain what the forward models predict."""

import json
from datetime import UTC, datetime

import numpy as np

from earwitness import cli
from earwitness.aircraft.model import doppler_curve
from earwitness.audio.decode import read_audio
from earwitness.audio.envelope import EnvelopeConfig, observed_envelope
from earwitness.audio.spectrogram import ridge, spectrogram
from earwitness.core import geodesy
from earwitness.simulate import MixOptions, write_aircraft_case, write_thunder_case
from earwitness.simulate.aircraft_synth import AircraftScenario, Flyover, build_tracks
from earwitness.simulate.thunder_synth import Strike, ThunderScenario, random_storm
from earwitness.thunder.model import ThunderConfig, model_envelope
from earwitness.thunder.search import cosine_similarity

START = datetime(2026, 7, 2, 21, 0, tzinfo=UTC)
LAT, LON = 35.0, -97.0
ORIGIN = np.zeros(3)


def _strike(time_s, bearing, distance):
    lat, lon = geodesy.destination(LAT, LON, bearing, distance)
    return Strike(time_s, float(lat), float(lon))


def test_thunder_case_envelope_matches_the_forward_model(tmp_path):
    cfg = ThunderConfig(smoothing_sigma_s=1.0)
    strikes = [
        _strike(5.0, 20.0, 6000.0),
        _strike(30.0, 95.0, 12000.0),
        _strike(55.0, 200.0, 9000.0),
        _strike(80.0, 300.0, 18000.0),
        _strike(110.0, 45.0, 4000.0),
        _strike(135.0, 160.0, 15000.0),
    ]
    scenario = ThunderScenario(LAT, LON, 0.0, START, 200.0, strikes, cfg)
    truth = write_thunder_case(
        tmp_path, scenario, 4000, MixOptions(snr_db=20.0), seed=1, rng=np.random.default_rng(1)
    )
    for name in ("recording.wav", "truth.json", "strikes.json"):
        assert (tmp_path / name).exists()
    assert truth["method"] == "thunder"
    assert len(truth["events"]) == 6
    assert all(e["heard"] for e in truth["events"])
    assert truth["start_time_utc"] == "2026-07-02T21:00:00.000Z"

    samples, rate = read_audio(tmp_path / "recording.wav")
    assert rate == 4000
    assert abs(samples.size / rate - 200.0) < 1e-9
    env = observed_envelope(samples, rate, EnvelopeConfig(smoothing_sigma_s=1.0))
    modeled = model_envelope(env.t, scenario.strike_set(), LAT, LON, cfg)
    score = cosine_similarity(env.values, modeled)
    assert score > 0.8, f"observed vs modeled envelope cosine {score:.3f}"

    geom = scenario.geometry()
    nearest = int(np.argmin(geom.slant_m))
    t_loudest = env.t[np.argmax(env.values)]
    assert abs(t_loudest - (geom.arrival_s[nearest] + 1.1)) < 1.5

    data = json.loads((tmp_path / "strikes.json").read_text())
    assert data["strikes"][0]["time_utc"] == "2026-07-02T21:00:05.000Z"
    assert data["strikes"][0]["kind"] == "CG"


def test_random_storm_geometry():
    rng = np.random.default_rng(3)
    strikes = random_storm(
        rng,
        LAT,
        LON,
        600.0,
        50,
        center_bearing_deg=45.0,
        center_distance_m=12000.0,
        radius_m=6000.0,
        drift_speed_mps=10.0,
        drift_bearing_deg=90.0,
        ic_fraction=0.3,
    )
    assert len(strikes) == 50
    times = np.array([s.time_s for s in strikes])
    assert np.all(np.diff(times) >= 0.0)
    assert times.min() >= -60.0
    assert times.max() <= 600.0
    kinds = {s.kind for s in strikes}
    assert kinds <= {"CG", "IC"}
    assert "IC" in kinds
    d = geodesy.horizontal_distance([s.lat for s in strikes], [s.lon for s in strikes], LAT, LON)
    assert 3000.0 < np.median(d) < 25000.0


def test_aircraft_case_spectrogram_ridge_follows_the_model(tmp_path):
    fly = Flyover(
        speed_mps=100.0,
        height_m=300.0,
        heading_deg=90.0,
        lateral_offset_m=0.0,
        t_cpa_s=60.0,
        f0_hz=130.0,
        harmonics=6,
    )
    scenario = AircraftScenario(LAT, LON, 0.0, START, 120.0, [fly])
    truth = write_aircraft_case(
        tmp_path, scenario, 4000, MixOptions(snr_db=0.0), seed=2, rng=np.random.default_rng(2)
    )
    for name in ("recording.wav", "truth.json", "tracks.json"):
        assert (tmp_path / name).exists()
    event = truth["events"][0]
    assert abs(event["cpa_slant_m"] - 300.0) < 1e-3
    assert abs(event["cpa_time_s"] - 60.0) < 1e-3
    assert abs(event["cpa_heard_time_s"] - (60.0 + 300.0 / 343.0)) < 1e-3

    samples, rate = read_audio(tmp_path / "recording.wav")
    spec = spectrogram(samples, rate, nfft=4096, hop=1024, fmax=500.0)
    track = build_tracks(scenario)[0]
    model = doppler_curve(track, ORIGIN, spec.t, 130.0)
    c, v = 343.0, 100.0
    _, f_peak = ridge(spec, 130.0 * c / (c + v) * 0.95, 130.0 * c / (c - v) * 1.05)
    away = np.abs(spec.t - event["cpa_heard_time_s"])
    # Within 25 s of closest approach the tone is strong: every frame must sit on the model.
    near = (away > 8.0) & (away < 25.0)
    err_near = np.abs(f_peak[near] - model.f[near])
    assert np.max(err_near) < 1.0, f"max ridge error near CPA {err_near.max():.2f} Hz"
    # Farther out the tone is faint and lingers near its asymptote, which median whitening
    # partly removes, so a rare frame may jump to a noise peak. Judge by percentiles.
    far = (away > 8.0) & (away < 40.0)
    err_far = np.abs(f_peak[far] - model.f[far])
    assert np.median(err_far) < 0.5, f"median ridge error {np.median(err_far):.2f} Hz"
    assert np.percentile(err_far, 95) < 1.0, f"p95 ridge error {np.percentile(err_far, 95):.2f}"

    data = json.loads((tmp_path / "tracks.json").read_text())
    aircraft = data["aircraft"]
    assert len(aircraft) == 1
    assert aircraft[0]["icao"] == fly.icao
    points = aircraft[0]["points"]
    assert abs(points[1]["t"] - points[0]["t"] - 1.0) < 1e-9
    assert abs(points[0]["t"] - (START.timestamp() + 60.0 - 600.0)) < 1e-6
    speeds = np.array([p["gs_mps"] for p in points])
    np.testing.assert_allclose(speeds, 100.0, atol=1e-6)


def test_two_flyover_case_has_two_tracks_and_the_right_gap(tmp_path):
    f1 = Flyover(70.0, 450.0, 10.0, 300.0, t_cpa_s=60.0, f0_hz=110.0, icao="ad5b01")
    f2 = Flyover(80.0, 400.0, 200.0, -250.0, t_cpa_s=150.0, f0_hz=95.0, icao="ad5b02")
    scenario = AircraftScenario(LAT, LON, 100.0, START, 240.0, [f1, f2], track_half_span_s=120.0)
    truth = write_aircraft_case(
        tmp_path, scenario, 4000, MixOptions(snr_db=-10.0), seed=5, rng=np.random.default_rng(5)
    )
    events = truth["events"]
    assert len(events) == 2
    gap = events[1]["cpa_heard_time_s"] - events[0]["cpa_heard_time_s"]
    expected = 90.0 + (f2.cpa_slant_m - f1.cpa_slant_m) / 343.0
    assert abs(gap - expected) < 1e-3
    data = json.loads((tmp_path / "tracks.json").read_text())
    assert [a["icao"] for a in data["aircraft"]] == ["ad5b01", "ad5b02"]
    altitudes = [p["alt_m"] for p in data["aircraft"][0]["points"]]
    assert min(altitudes) > 100.0 + 450.0 - 1e-6  # above the observer's elevation everywhere
    assert truth["observer"]["elevation_m"] == 100.0


def test_cli_simulate_thunder_writes_a_case(tmp_path, capsys):
    out = tmp_path / "case"
    args = [
        "simulate", "thunder", "--lat", "35", "--lon", "-97",
        "--start", "2026-07-02T21:00:00Z", "--minutes", "2", "--out", str(out),
        "--seed", "3", "--sample-rate", "4000", "--n-strikes", "6", "--storm-distance-km", "8",
    ]  # fmt: skip
    assert cli.main(args) == 0
    for name in ("recording.wav", "truth.json", "strikes.json"):
        assert (out / name).exists()
    truth = json.loads((out / "truth.json").read_text())
    assert truth["seed"] == 3
    assert len(truth["events"]) == 6
    assert truth["recording"]["sample_rate_hz"] == 4000
    assert truth["recording"]["duration_s"] == 120.0
    assert "6 strikes" in capsys.readouterr().out


def test_cli_simulate_thunder_from_a_strikes_file(tmp_path):
    strikes = {
        "strikes": [
            {"time_utc": "2026-07-02T21:00:10Z", "lat": 35.05, "lon": -97.0, "kind": "CG"},
            {"time_s": 40.0, "lat": 35.0, "lon": -96.9, "kind": "IC"},
        ]
    }
    sfile = tmp_path / "s.json"
    sfile.write_text(json.dumps(strikes))
    out = tmp_path / "case"
    args = [
        "simulate", "thunder", "--lat", "35", "--lon", "-97",
        "--start", "2026-07-02T21:00:00Z", "--minutes", "2", "--out", str(out),
        "--seed", "1", "--sample-rate", "4000", "--strikes", str(sfile), "--noise", "none",
    ]  # fmt: skip
    assert cli.main(args) == 0
    truth = json.loads((out / "truth.json").read_text())
    events = truth["events"]
    assert events[0]["time_s"] == 10.0
    assert events[1]["kind"] == "IC"
    assert events[1]["time_s"] == 40.0
    assert truth["mix"]["snr_db"] is None


def test_cli_simulate_aircraft_writes_a_case(tmp_path, capsys):
    out = tmp_path / "case"
    args = [
        "simulate", "aircraft", "--lat", "35", "--lon", "-97",
        "--start", "2026-09-08T18:00:00Z", "--minutes", "4", "--out", str(out),
        "--seed", "7", "--sample-rate", "4000", "--flyovers", "2", "--gap-s", "90",
        "--heading", "45",
    ]  # fmt: skip
    assert cli.main(args) == 0
    truth = json.loads((out / "truth.json").read_text())
    assert truth["method"] == "aircraft"
    assert len(truth["events"]) == 2
    assert truth["events"][0]["heading_deg"] == 45.0
    assert truth["events"][0]["icao"] == "ad5b01"
    assert (out / "tracks.json").exists()
    assert "flyover ad5b01" in capsys.readouterr().out


def test_cli_simulate_aircraft_rejects_a_short_clip(tmp_path, capsys):
    args = [
        "simulate", "aircraft", "--lat", "35", "--lon", "-97",
        "--start", "2026-09-08T18:00:00Z", "--minutes", "2", "--out", str(tmp_path / "c"),
        "--flyovers", "3", "--gap-s", "600",
    ]  # fmt: skip
    assert cli.main(args) == 2
    assert "too short" in capsys.readouterr().err


def test_cli_unimplemented_commands_exit_3(capsys):
    assert cli.main(["analyze", "aircraft", "x.wav"]) == 3
    assert cli.main(["ui"]) == 3
    assert "planned for" in capsys.readouterr().err
