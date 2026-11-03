"""Lightning event files in both formats, and the container's filters."""

import json
from datetime import UTC, datetime

import numpy as np
import pytest

from earwitness.core.grid import BBox
from earwitness.data.adapters import LightningEvents, load_events, save_events

T0 = datetime(2026, 7, 2, 21, 0, tzinfo=UTC).timestamp()


def _events():
    return LightningEvents(
        time_unix=np.array([T0 + 5.0, T0 + 65.5, T0 + 200.25]),
        lat=np.array([35.0, 35.1, 36.0]),
        lon=np.array([-97.0, -97.1, -96.0]),
        kind=np.array(["CG", "IC", "CG"]),
        energy=np.array([1e-14, 2e-14, 3e-14]),
        source="test",
    )


def test_json_roundtrip_keeps_millisecond_times(tmp_path):
    path = save_events(tmp_path / "s.json", _events())
    raw = json.loads(path.read_text())
    assert raw["schema"] == "earwitness.strikes/1"
    assert raw["strikes"][2]["time_utc"] == "2026-07-02T21:03:20.250Z"
    back = load_events(path)
    np.testing.assert_allclose(back.time_unix, _events().time_unix, atol=1e-3)
    assert back.kind.tolist() == ["CG", "IC", "CG"]
    np.testing.assert_allclose(back.energy, [1e-14, 2e-14, 3e-14])


def test_parquet_roundtrip(tmp_path):
    path = save_events(tmp_path / "s.parquet", _events())
    back = load_events(path)
    np.testing.assert_allclose(back.time_unix, _events().time_unix, atol=1e-6)
    np.testing.assert_allclose(back.lat, [35.0, 35.1, 36.0])
    assert back.kind.tolist() == ["CG", "IC", "CG"]


def test_load_truth_json_events_and_relative_times(tmp_path):
    truth = {
        "events": [
            {
                "time_utc": "2026-07-02T21:00:05Z",
                "lat": 35.0,
                "lon": -97.0,
                "kind": "CG",
                "heard": True,
            }
        ]
    }
    p = tmp_path / "truth.json"
    p.write_text(json.dumps(truth))
    ev = load_events(p)
    assert len(ev) == 1
    assert abs(ev.time_unix[0] - (T0 + 5.0)) < 1e-6
    rel = tmp_path / "rel.json"
    rel.write_text(json.dumps([{"time_s": 40.0, "lat": 35.0, "lon": -97.0}]))
    ev2 = load_events(rel, t_ref_unix=T0)
    assert abs(ev2.time_unix[0] - (T0 + 40.0)) < 1e-6
    with pytest.raises(ValueError, match="time_utc"):
        load_events(rel)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"nothing": []}))
    with pytest.raises(ValueError, match="unrecognized"):
        load_events(bad)


def test_filters_and_strike_set():
    ev = _events()
    assert len(ev.within(BBox(-97.5, 34.5, -96.5, 35.5))) == 2
    assert len(ev.between(T0, T0 + 100.0)) == 2
    assert ev.bbox() == BBox(-97.1, 35.0, -96.0, 36.0)
    strikes = ev.to_strike_set(T0)
    np.testing.assert_allclose(strikes.time_s, [5.0, 65.5, 200.25])
    assert strikes.kind.tolist() == ["CG", "IC", "CG"]
    shuffled = ev.subset(np.array([2, 0, 1]))
    assert shuffled.sorted_by_time().time_unix.tolist() == ev.time_unix.tolist()
    with pytest.raises(ValueError, match="no events"):
        ev.subset(np.zeros(3, dtype=bool)).bbox()
