"""One-step locate: time priors, regions, confidence words, and a synthetic case end to end."""

import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from earwitness import cli
from earwitness.aircraft.prep import flights_from_tracks_json
from earwitness.core import geodesy
from earwitness.locate import (
    LocateOptions,
    TimePrior,
    confidence,
    locate,
    region_around,
)
from earwitness.simulate import MixOptions, write_aircraft_case
from earwitness.simulate.aircraft_synth import AircraftScenario, Flyover

START = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
LAT, LON = 35.0, -97.0


def test_time_prior_forms():
    with pytest.raises(ValueError, match="timezone-aware"):
        TimePrior.at(datetime(2026, 9, 8, 18, 0))
    exact = TimePrior.at(START, 2.0)
    assert exact.exact
    assert exact.start == START
    assert exact.window_end - exact.window_start == timedelta(seconds=4)
    assert exact.search_window() == (START.replace(hour=0), START.replace(hour=0) + timedelta(1))
    rough = TimePrior.at(START, 600.0)
    assert not rough.exact
    day = TimePrior.days(date(2026, 9, 8))
    assert day.start is None
    assert not day.exact
    assert day.window_end - day.window_start == timedelta(days=1)
    # a clip that may have started either side of midnight needs both days
    late = TimePrior.at(datetime(2026, 9, 8, 23, 59, 59, tzinfo=UTC), 2.0)
    lo, hi = late.search_window()
    assert hi - lo == timedelta(days=2)
    # flight files: the clip's own days with a known start, only the named days otherwise
    assert late.flight_days(600.0) == [date(2026, 9, 8), date(2026, 9, 9)]
    assert exact.flight_days(600.0) == [date(2026, 9, 8)]
    assert day.flight_days(600.0) == [date(2026, 9, 8)]
    assert TimePrior.days(date(2026, 9, 8), date(2026, 9, 9)).flight_days(60.0) == [
        date(2026, 9, 8),
        date(2026, 9, 9),
    ]


def test_region_around_a_point():
    box = region_around(LAT, LON, 10000.0)
    assert bool(box.contains(LAT, LON))
    assert box.area_km2() == pytest.approx(400.0, rel=0.02)
    with pytest.raises(ValueError, match="radius"):
        region_around(LAT, LON, 0.0)


def test_confidence_words_follow_the_validated_margins():
    assert confidence("aircraft", 3.3)[0] == "clear"
    assert confidence("aircraft", 0.5)[0] == "probable"
    assert confidence("aircraft", 0.13)[0] == "unsettled"
    assert confidence("thunder", 0.084)[0] == "possible"
    assert confidence("thunder", 0.031)[0] == "unsettled"
    assert confidence("thunder", 5.0)[0] == "possible"
    assert confidence("aircraft", None)[0] == "probable"
    assert confidence("thunder", None)[0] == "possible"


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    flyovers = [
        Flyover(70.0, 400.0, 90.0, 300.0, t_cpa_s=300.0, f0_hz=110.0, harmonics=6, icao="ad5b01"),
        Flyover(85.0, 500.0, 200.0, -250.0, t_cpa_s=540.0, f0_hz=95.0, harmonics=6, icao="ad5b02"),
    ]
    scenario = AircraftScenario(LAT, LON, 0.0, START, 900.0, flyovers, track_half_span_s=400.0)
    folder = tmp_path_factory.mktemp("locate") / "case"
    write_aircraft_case(
        folder, scenario, 4000, MixOptions(snr_db=-15.0), 21, np.random.default_rng(21)
    )
    flights = flights_from_tracks_json(folder / "tracks.json")
    decoys = flights_from_tracks_json(folder / "tracks.json")
    for d, (dt, dlat) in zip(decoys, [(2000.0, 0.08), (-1500.0, -0.005)], strict=True):
        d.icao = "decoy" + d.icao[-1]
        d.time_unix = d.time_unix + dt
        d.lat = d.lat + dlat
    return folder, flights + decoys


def test_locate_a_synthetic_two_flyover_recording(case, tmp_path):
    folder, flights = case
    summary = locate(
        folder / "recording.wav",
        TimePrior.days(START.date()),
        region_around(LAT, LON, 15000.0),
        tmp_path / "out",
        options=LocateOptions(methods=("aircraft",), fetch=False, terrain=False),
        flights=flights,
    )
    assert summary["events_used"] == ["auto1", "auto2"]
    assert len(summary["flyovers"]) >= 2
    answer = summary["answer"]
    assert answer is not None
    assert answer["method"] == "aircraft"
    err = float(geodesy.horizontal_distance(answer["lat"], answer["lon"], LAT, LON))
    assert err < 200.0
    assert answer["level"] in ("clear", "probable")
    saved = json.loads((tmp_path / "out" / "locate.json").read_text(encoding="utf-8"))
    assert saved["answer"]["lat"] == answer["lat"]
    assert (tmp_path / "out" / "aircraft" / "events.json").exists()


def test_locate_without_a_clock_skips_a_lone_flyover(case, tmp_path):
    folder, flights = case
    summary = locate(
        folder / "recording.wav",
        TimePrior.days(START.date()),
        region_around(LAT, LON, 15000.0),
        tmp_path / "one",
        options=LocateOptions(methods=("aircraft",), max_events=1, fetch=False, terrain=False),
        flights=flights,
    )
    [aircraft] = summary["methods"]
    assert aircraft["status"] == "skipped"
    assert "known start time" in aircraft["reason"]
    assert summary["answer"] is None


def test_measure_cli_writes_events(case, tmp_path, capsys):
    folder, _ = case
    out = tmp_path / "events.json"
    assert cli.main(["measure", str(folder / "recording.wav"), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "propeller flyover(s)" in printed
    events = json.loads(out.read_text(encoding="utf-8"))
    assert [e["id"] for e in events["events"]] == ["auto1", "auto2"]


def test_locate_cli_needs_a_place(case, capsys):
    folder, _ = case
    assert cli.main(["locate", str(folder / "recording.wav")]) == 2
    assert "--near" in capsys.readouterr().err
