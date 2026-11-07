"""Automatic flyover measurement on synthetic recordings with known flyovers."""

from datetime import UTC, datetime

import numpy as np
import pytest

from earwitness.aircraft.detect import (
    RATE_HZ,
    DetectConfig,
    Detection,
    auto_events,
    detect_flyovers,
    detection_spectrogram,
    runs_at_least,
)
from earwitness.core.physics import SPEED_OF_SOUND
from earwitness.simulate.aircraft_synth import AircraftScenario, Flyover, synthesize_aircraft
from earwitness.simulate.mix import add_noise

START = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
FLYOVERS = [
    Flyover(70.0, 400.0, 90.0, 300.0, t_cpa_s=300.0, f0_hz=110.0, harmonics=6),
    Flyover(85.0, 500.0, 200.0, -250.0, t_cpa_s=540.0, f0_hz=95.0, harmonics=6),
]


def _record(flyovers, duration_s=900.0, snr_db=-15.0, seed=5):
    scenario = AircraftScenario(35.0, -97.0, 0.0, START, duration_s, flyovers)
    clean = synthesize_aircraft(scenario, RATE_HZ)
    return add_noise(clean, snr_db, np.random.default_rng(seed))


def _heard(f: Flyover) -> float:
    return f.t_cpa_s + f.cpa_slant_m / SPEED_OF_SOUND


@pytest.fixture(scope="module")
def two_flyovers() -> Detection:
    cfg = DetectConfig()
    return detect_flyovers(detection_spectrogram(_record(FLYOVERS), RATE_HZ, cfg), cfg)


def test_runs_at_least_keeps_only_lasting_runs():
    frames = np.array([0, 1, 2, 3, 10, 11, 20, 21, 23, 24, 25, 26])
    keep = runs_at_least(frames, min_frames=4, max_gap=2)
    # 0-3 spans 4 frames; 10-11 only 2; 20-26 spans 7 with a one-frame gap
    assert keep.tolist() == [True] * 4 + [False] * 2 + [True] * 6


def test_both_flyovers_are_found_and_measured(two_flyovers):
    found = two_flyovers.flyovers
    assert len(found) >= 2
    strongest = sorted(found[:2], key=lambda f: f.heard_s)
    for fly, truth in zip(strongest, FLYOVERS, strict=True):
        assert abs(fly.heard_s - _heard(truth)) < 3.0
        fit = fly.line.fit
        # the clearest line is one of the six harmonics
        n = round(fit.f0_hz / truth.f0_hz)
        assert 1 <= n <= 6
        assert abs(fit.f0_hz / (n * truth.f0_hz) - 1.0) < 0.02
        # a straight synthetic track: the fit lands on the truth's slope at closest approach,
        # v^2 / d, which is what the trace fixes best
        slope = fit.v_mps**2 / fit.d_m
        assert slope == pytest.approx(truth.speed_mps**2 / truth.cpa_slant_m, rel=0.25)


def test_harmonics_are_one_aircraft_not_six(two_flyovers):
    found = two_flyovers.flyovers
    real = sorted(found[:2], key=lambda f: f.heard_s)
    assert all(len(f.others) >= 1 for f in real)
    weaker = min(f.quality for f in real)
    # anything else the noise produced is far weaker than either real flyover
    assert all(f.quality < 0.25 * weaker for f in found[2:])


def test_road_traffic_is_not_a_flyover():
    car = Flyover(25.0, 2.0, 45.0, 20.0, t_cpa_s=60.0, f0_hz=120.0, harmonics=6)
    cfg = DetectConfig()
    det = detect_flyovers(
        detection_spectrogram(_record([car], duration_s=120.0, snr_db=-5.0), RATE_HZ, cfg), cfg
    )
    assert det.flyovers == []


def test_auto_events_from_two_flyovers(two_flyovers):
    events = auto_events(two_flyovers, recording={"file": "x.wav"})
    assert events is not None
    assert [e.id for e in events.events] == ["auto1", "auto2"]
    assert events.known_time is None
    for e, truth in zip(events.events, FLYOVERS, strict=True):
        assert e.t0_s.contains(_heard(truth), margin=1.0)
        assert e.v_mps.contains(truth.speed_mps)
        assert e.d_m.contains(truth.cpa_slant_m)
        assert e.profiled
        assert e.trace is not None
        assert e.trace.shape[1] == 2
    gap = events.gap()
    assert gap.contains(_heard(FLYOVERS[1]) - _heard(FLYOVERS[0]))


def test_one_flyover_needs_a_known_time(two_flyovers):
    one = Detection(flyovers=two_flyovers.flyovers[:1], counts={})
    assert auto_events(one) is None
    known = datetime(2026, 9, 8, 18, 0, 5, tzinfo=UTC)
    events = auto_events(one, known_time=known, known_time_tolerance_s=2.0)
    assert events is not None
    assert events.single_event
    assert events.known_time == known
    assert events.known_time_tolerance_s == 2.0


def test_nothing_found_gives_no_events():
    assert auto_events(Detection(flyovers=[], counts={})) is None


def test_a_flyover_at_the_clip_edge_is_the_last_choice(two_flyovers):
    # pretend the clip ends 18 s after the second flyover's crossing: half its curve is gone
    first, second = sorted(two_flyovers.flyovers[:2], key=lambda f: f.heard_s)
    cut = Detection(
        flyovers=[second, first], counts={}, duration_s=second.heard_s + 18.0, edge_s=30.0
    )
    assert cut.usable() == [first]
    known = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
    one = auto_events(cut, max_events=1, known_time=known)
    assert one is not None
    assert one.events[0].t0_s.contains(first.heard_s)
    # without a clock two events are needed, so the edge flyover fills the second place
    two = auto_events(cut, max_events=2)
    assert two is not None
    assert len(two.events) == 2
