"""The audit report finds the clues the simulator put in and reports nothing on plain noise."""

import json
from datetime import UTC, datetime

import numpy as np

from earwitness import cli
from earwitness.audio.decode import write_wav
from earwitness.audit.report import audit_file, mains_hum_clue
from earwitness.simulate import MixOptions, write_aircraft_case, write_thunder_case
from earwitness.simulate.aircraft_synth import AircraftScenario, Flyover
from earwitness.simulate.thunder_synth import ThunderScenario, random_storm

LAT, LON = 35.0, -97.0
RATE = 4000


def _thunder_case(tmp_path, seed=11):
    rng = np.random.default_rng(seed)
    strikes = []
    for bearing, distance, n in [(50.0, 9000.0, 18), (200.0, 12000.0, 18)]:
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
    start = datetime(2026, 7, 2, 21, 0, tzinfo=UTC)
    scenario = ThunderScenario(LAT, LON, 0.0, start, 900.0, strikes)
    case = tmp_path / "thunder"
    write_thunder_case(case, scenario, RATE, MixOptions(snr_db=10.0), seed, rng)
    return case


def _aircraft_case(tmp_path, seed=21):
    flyovers = [
        Flyover(70.0, 400.0, 90.0, 300.0, t_cpa_s=300.0, f0_hz=110.0, harmonics=6, icao="ad5b01"),
        Flyover(85.0, 500.0, 200.0, -250.0, t_cpa_s=540.0, f0_hz=95.0, harmonics=6, icao="ad5b02"),
    ]
    start = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
    scenario = AircraftScenario(LAT, LON, 0.0, start, 900.0, flyovers, track_half_span_s=400.0)
    case = tmp_path / "aircraft"
    write_aircraft_case(
        case, scenario, RATE, MixOptions(snr_db=-15.0), seed, np.random.default_rng(seed)
    )
    return case


def _noise_wav(path, seconds=120.0, seed=5):
    rng = np.random.default_rng(seed)
    x = 0.05 * rng.standard_normal(int(seconds * RATE))
    return write_wav(path, x, RATE)


def _clues(report):
    return {c.kind: c for c in report.clues}


def test_thunder_case_shows_bursts_and_no_aircraft(tmp_path):
    case = _thunder_case(tmp_path)
    report = audit_file(case / "recording.wav")
    clues = _clues(report)
    assert clues["thunder"].present
    assert clues["thunder"].evidence["bursts"] >= 5
    assert clues["thunder"].confidence == "likely"
    assert not clues["aircraft"].present
    assert not clues["mains_hum"].present
    assert report.any_present
    assert "thunder" in report.text()


def test_aircraft_case_shows_falling_tonal_sweeps(tmp_path):
    case = _aircraft_case(tmp_path)
    clues = _clues(audit_file(case / "recording.wav"))
    assert clues["aircraft"].present
    assert clues["aircraft"].evidence["sweeps"] >= 1
    segment = clues["aircraft"].evidence["segments"][0]
    assert segment["f_start_hz"] > segment["f_end_hz"]
    assert segment["drop_pct"] >= 2.0
    assert not clues["mains_hum"].present


def test_plain_noise_has_no_clues(tmp_path):
    wav = _noise_wav(tmp_path / "noise.wav")
    report = audit_file(wav)
    clues = _clues(report)
    for kind in ("thunder", "aircraft", "mains_hum"):
        assert not clues[kind].present, kind
    assert not report.any_present
    assert "No clue" in report.text()


def test_mains_hum_is_detected_with_its_harmonics():
    rng = np.random.default_rng(7)
    t = np.arange(int(60 * RATE)) / RATE
    x = 0.05 * rng.standard_normal(t.size)
    x += 0.02 * np.sin(2 * np.pi * 60.0 * t)
    x += 0.01 * np.sin(2 * np.pi * 120.0 * t)
    x += 0.005 * np.sin(2 * np.pi * 180.0 * t)
    clue = mains_hum_clue(x, RATE)
    assert clue.present
    assert clue.confidence == "likely"
    assert clue.evidence["nominal_hz"] == 60
    assert clue.evidence["lines_db"]["60"][0] > 8.0
    assert "60 Hz" in clue.summary


def test_report_dict_has_the_schema_and_all_clue_kinds(tmp_path):
    wav = _noise_wav(tmp_path / "noise.wav", seconds=30.0)
    report = audit_file(wav).to_dict()
    assert report["schema"] == "earwitness.audit/1"
    assert [c["kind"] for c in report["clues"]] == ["metadata", "thunder", "aircraft", "mains_hum"]
    assert report["file"]["name"] == "noise.wav"
    assert abs(report["file"]["duration_s"] - 30.0) < 1e-6


def test_cli_audit_prints_text_and_json(tmp_path, capsys):
    wav = _noise_wav(tmp_path / "noise.wav", seconds=30.0)
    assert cli.main(["audit", str(wav)]) == 0
    out = capsys.readouterr().out
    assert "Earwitness audit: noise.wav" in out
    assert "mains hum" in out
    assert cli.main(["audit", str(wav), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["clues"]) == 4
    assert cli.main(["audit", str(tmp_path / "missing.wav")]) == 2
