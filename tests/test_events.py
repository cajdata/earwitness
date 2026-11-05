"""Event ranges, the events.json schema, and measurement ranges derived from a truth file."""

import json
from datetime import UTC, datetime

import numpy as np
import pytest

from earwitness.audio import events as ev


def test_range_basics():
    r = ev.Range(90.0, 110.0)
    assert r.mid == 100.0
    assert r.half_width == 10.0
    assert r.contains(95.0)
    assert not r.contains(111.0)
    assert r.contains(111.0, margin=2.0)
    assert ev.Range.around(5.0, 10.0, lo_floor=1.0) == ev.Range(1.0, 15.0)
    with pytest.raises(ValueError, match="upside down"):
        ev.Range(2.0, 1.0)


def test_event_set_roundtrip(tmp_path):
    e1 = ev.FlyoverEvent(
        "e1", ev.Range(280, 290), ev.Range(100, 120), ev.Range(60, 80), ev.Range(200, 800)
    )
    e2 = ev.FlyoverEvent(
        "e2", ev.Range(1200, 1210), ev.Range(90, 100), ev.Range(50, 70), ev.Range(300, 900),
        harmonics=2, trace=np.array([[1195.0, 101.0], [1205.0, 95.0]]),
    )  # fmt: skip
    es = ev.EventSet(
        [e1, e2],
        [ev.SilenceWindow(0.0, 200.0), ev.SilenceWindow(400.0, 1100.0)],
        known_time=datetime(2026, 9, 8, 18, 0, tzinfo=UTC),
        known_time_tolerance_s=2.0,
        recording={"file": "clip.wav"},
    )
    path = es.save(tmp_path / "events.json")
    raw = json.loads(path.read_text())
    assert raw["schema"] == "earwitness.events/1"
    assert raw["events"][0]["t0_s"] == [280.0, 290.0]
    back = ev.EventSet.load(path)
    assert [e.id for e in back.events] == ["e1", "e2"]
    assert back.events[1].harmonics == 2
    np.testing.assert_allclose(back.events[1].trace, e2.trace)
    assert back.known_time == es.known_time
    assert back.known_time_tolerance_s == 2.0
    assert back.gap() == ev.Range(1200 - 290, 1210 - 280)
    assert not back.single_event
    with pytest.raises(ValueError, match="at least one"):
        ev.EventSet([])
    with pytest.raises(ValueError, match="unique"):
        ev.EventSet([e1, e1])


def test_silence_from_audible_intervals():
    audible = ev.audible_intervals(
        np.array([300.0, 320.0, 900.0]), np.array([40.0, 40.0, 50.0]), 1200.0
    )
    assert audible == [(260.0, 360.0), (850.0, 950.0)]
    silence = ev.silence_between(audible, 1200.0, min_length_s=30.0)
    assert [(s.start_s, s.end_s) for s in silence] == [
        (0.0, 260.0),
        (360.0, 850.0),
        (950.0, 1200.0),
    ]
    assert ev.silence_between([(0.0, 1200.0)], 1200.0) == []


def _flyover(heard, slant, speed, f0):
    return {
        "kind": "flyover",
        "cpa_heard_time_s": heard,
        "cpa_slant_m": slant,
        "speed_mps": speed,
        "f0_hz": f0,
    }


def test_events_from_truth_picks_closest_flyovers_in_time_order():
    truth = {
        "recording": {"file": "recording.wav", "duration_s": 1200.0},
        "physics": {"speed_of_sound_mps": 343.0},
        "events": [
            _flyover(900.0, 400.0, 70.0, 110.0),
            _flyover(300.0, 500.0, 80.0, 95.0),
            _flyover(600.0, 2500.0, 60.0, 100.0),
            _flyover(1500.0, 100.0, 60.0, 100.0),
        ],
    }
    es = ev.events_from_truth(truth)
    assert [e.id for e in es.events] == ["e1", "e2"]
    assert es.events[0].t0_s == ev.Range(297.0, 303.0)
    assert es.events[0].f0_hz == ev.Range(92.0, 98.0)
    assert es.events[0].v_mps == ev.Range(68.0, 92.0)
    assert es.events[0].d_m == ev.Range(250.0, 750.0)
    assert es.events[1].t0_s.mid == 900.0
    assert es.gap().mid == 600.0
    # silence: before the first flyover, between them, and after the last; the 2.5 km one is
    # audible within the 3 km range for a short stretch around 600 s
    assert es.silence[0].start_s == 0.0
    assert es.silence[0].end_s < 297.0
    assert any(s.start_s > 303.0 and s.end_s < 897.0 for s in es.silence)
    single = ev.events_from_truth(truth, max_events=1)
    assert single.single_event
    assert single.events[0].d_m.mid == 400.0


def test_events_from_truth_uses_given_audible_intervals_and_edge_margin():
    truth = {
        "recording": {"file": "recording.wav", "duration_s": 1200.0},
        "physics": {"speed_of_sound_mps": 343.0},
        "audible_intervals_s": [[250.0, 360.0], [340.0, 700.0], [1150.0, 1300.0]],
        "events": [
            _flyover(300.0, 500.0, 80.0, 95.0),
            _flyover(600.0, 2500.0, 60.0, 100.0),
            _flyover(1180.0, 100.0, 60.0, 100.0),  # closest, but too near the end
        ],
    }
    es = ev.events_from_truth(truth, max_events=1, harmonics=3, edge_margin_s=60.0)
    assert es.events[0].t0_s.mid == 300.0
    assert es.events[0].harmonics == 3
    assert [(s.start_s, s.end_s) for s in es.silence] == [(0.0, 250.0), (700.0, 1150.0)]
    assert ev.merge_spans([(5.0, 2.0), (-10.0, 20.0), (15.0, 30.0)], 25.0) == [(0.0, 25.0)]


def test_valley_narrows_ranges_to_a_speed():
    valley = np.array([[50.0, 250.0, 150.0, 3058.0], [100.0, 1000.0, 130.0, 3067.0],
                       [200.0, 4000.0, 80.0, 3077.0]])  # fmt: skip
    e = ev.FlyoverEvent(
        "e2", ev.Range(3055.0, 3080.0), ev.Range(60.0, 160.0), ev.Range(40.0, 230.0),
        ev.Range(100.0, 9000.0), valley=valley,
    )  # fmt: skip
    r = e.at_speed([100.0, 141.42, 20.0, 400.0])
    np.testing.assert_allclose(r["d_m"][0][0], 500.0)  # 1000 m at 100 m/s, minus 50%
    np.testing.assert_allclose(r["d_m"][1][0], 1500.0)
    np.testing.assert_allclose(0.5 * (r["d_m"][0][1] + r["d_m"][1][1]), 2000.0, rtol=0.01)
    np.testing.assert_allclose(r["f0_hz"][0][0], 127.0)
    np.testing.assert_allclose(r["t0_s"][1][0], 3070.0)
    # beyond the rows the end rows hold, and the envelope clips
    np.testing.assert_allclose(r["d_m"][0][2], 125.0)
    np.testing.assert_allclose(r["d_m"][1][3], 6000.0)
    n = e.narrowed(100.0)
    np.testing.assert_allclose([n.d_m.lo, n.d_m.hi], [500.0, 1500.0])
    np.testing.assert_allclose([n.f0_hz.lo, n.f0_hz.hi], [127.0, 133.0])
    np.testing.assert_allclose([n.t0_s.lo, n.t0_s.hi], [3064.0, 3070.0])
    assert n.v_mps == e.v_mps
    # round trip through JSON, and an event without a valley narrows to itself
    back = ev.FlyoverEvent.from_dict(e.to_dict())
    np.testing.assert_allclose(back.valley, valley)
    plain = ev.FlyoverEvent("e1", ev.Range(1, 2), ev.Range(1, 2), ev.Range(1, 2), ev.Range(1, 2))
    assert plain.narrowed(50.0) is plain
    assert ev.FlyoverEvent.from_dict(plain.to_dict()).valley is None
