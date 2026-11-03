"""adsb.lol day loader on a synthetic split tar built in the test."""

import gzip
import io
import json
import tarfile
from datetime import UTC, date, datetime

import numpy as np
import pyarrow.parquet as pq

from earwitness.core.grid import BBox
from earwitness.data import adsb_lol as al

DAY = date(2026, 9, 8)
T0 = datetime(2026, 9, 8, tzinfo=UTC).timestamp()


def _trace_doc(icao, points, t="C172", desc="CESSNA 172", r="N12345", db_flags=0):
    doc = {
        "icao": icao,
        "r": r,
        "t": t,
        "desc": desc,
        "dbFlags": db_flags,
        "timestamp": T0,
        "trace": points,
    }
    return gzip.compress(json.dumps(doc).encode())


def _point(dt, lat, lon, alt=3500, gs=100.0, track=90.0, flags=0, vrate=0, alt_geom=3450):
    return [
        dt,
        lat,
        lon,
        alt,
        gs,
        track,
        flags,
        vrate,
        None,
        "adsb_icao",
        alt_geom,
        None,
        None,
        None,
    ]


def _build_parts(tmp_path):
    inside = [_point(60.0 + i * 5.0, 35.0 + 0.001 * i, -97.0 + 0.002 * i) for i in range(20)]
    inside[3][6] = 1  # a stale position flag
    inside[0][3] = "ground"  # first point on the ground
    outside = [_point(100.0 + i, 51.7, -113.9) for i in range(10)]
    mixed = [_point(200.0 + i * 2.0, 35.5 if i < 6 else 60.0, -96.5) for i in range(12)]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:

        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        add("./LICENSE-cc0.txt", b"cc0")
        add("./heatmap/00.bin.ttf", b"\x00" * 64)
        add("./traces/00/trace_full_a00000.json", _trace_doc("a00000", inside))
        add("./traces/42/trace_full_a03a42.json", _trace_doc("a03a42", outside, t="B738"))
        add(
            "./traces/42/trace_full_~28f542.json", _trace_doc("~28f542", mixed, t="", r="", desc="")
        )
        add(
            "./traces/42/trace_full_empty.json",
            gzip.compress(b'{"icao": "000001", "timestamp": 0, "trace": []}'),
        )
    data = buf.getvalue()
    cut = len(data) // 2 + 123
    parts = [tmp_path / "day.tar.aa", tmp_path / "day.tar.ab"]
    parts[0].write_bytes(data[:cut])
    parts[1].write_bytes(data[cut:])
    return parts


def test_names_and_urls():
    assert al.release_tag(DAY) == "v2026.09.08-planes-readsb-prod-0"
    assert al.repo_for(DAY) == "adsblol/globe_history_2026"
    assert al.asset_url(DAY, "x.tar.aa").endswith("/v2026.09.08-planes-readsb-prod-0/x.tar.aa")
    assert al.history_trace_url(DAY, "A03A42") == (
        "https://adsb.lol/globe_history/2026/09/08/traces/42/trace_full_a03a42.json"
    )
    assert al.region_key(BBox(-125.0, 24.0, -66.0, 50.0)) == "m125.00_24.00_m66.00_50.00"


def test_chained_reader_and_trace_iteration(tmp_path):
    parts = _build_parts(tmp_path)
    names = [name for name, _ in al.iter_trace_files(parts)]
    assert names == [
        "./traces/00/trace_full_a00000.json",
        "./traces/42/trace_full_a03a42.json",
        "./traces/42/trace_full_~28f542.json",
        "./traces/42/trace_full_empty.json",
    ]


def test_parse_trace_fields():
    raw = _trace_doc(
        "a00000", [_point(10.5, 35.0, -97.0), _point(12.0, 35.1, -97.1, alt="ground", flags=2)]
    )
    tr = al.parse_trace(raw)
    assert tr is not None
    assert tr.icao == "a00000"
    assert tr.type_code == "C172"
    assert tr.registration == "N12345"
    np.testing.assert_allclose(tr.time_unix, [T0 + 10.5, T0 + 12.0])
    assert np.isnan(tr.alt_baro_ft[1])
    assert tr.on_ground[1]
    assert not tr.on_ground[0]
    assert tr.alt_geom_ft[0] == 3450.0
    assert tr.flags[1] == 2
    assert tr.source == ["adsb_icao", "adsb_icao"]
    assert al.parse_trace(gzip.compress(b'{"icao": "x", "timestamp": 0, "trace": []}')) is None
    plain = json.dumps(
        {
            "icao": "b",
            "timestamp": 5.0,
            "trace": [
                [1.0, 1.0, 2.0, None, None, None, 0, None, None, None, None, None, None, None]
            ],
        }
    ).encode()
    tp = al.parse_trace(plain)
    assert tp is not None
    assert np.isnan(tp.alt_baro_ft[0])
    assert np.isnan(tp.gs_kt[0])
    assert tp.time_unix[0] == 6.0


def test_build_and_load_day_from_parts(tmp_path):
    parts = _build_parts(tmp_path)
    root = tmp_path / "cache"
    box = BBox(-100.0, 30.0, -90.0, 40.0)
    stats = al.build_day(DAY, box, root, parts=parts, keep_raw=True, flush_points=5)
    assert stats.traces_read == 4
    assert stats.aircraft_kept == 2  # a00000 fully inside, ~28f542 partly inside; a03a42 outside
    assert stats.points_kept == 20 + 6
    assert stats.path == al.day_cache_path(root, DAY, box)
    assert all(p.exists() for p in parts)  # keep_raw

    table = pq.read_table(stats.path)
    assert table.schema.equals(al.POINT_SCHEMA)
    icaos = table.column("icao").to_pylist()
    assert icaos.count("a00000") == 20
    assert icaos.count("~28f542") == 6
    assert table.column("type_code").to_pylist()[:1] == ["C172"]

    loaded = al.load_day(DAY, box, root)
    assert loaded.num_rows == 26
    later = al.load_day(DAY, box, root, t_start=datetime(2026, 9, 8, 0, 2, tzinfo=UTC))
    assert (
        later.num_rows == 20 - 12 + 6
    )  # a00000 points from t=120 s on, plus the mixed ones at 200 s
    narrow = al.load_day(DAY, box, root, sub_bbox=BBox(-97.0, 35.4, -96.0, 36.0))
    assert narrow.num_rows == 6


def test_clip_trace_requires_min_points():
    tr = al.parse_trace(_trace_doc("a00000", [_point(1.0, 35.0, -97.0), _point(2.0, 60.0, -97.0)]))
    assert al.clip_trace(tr, BBox(-98.0, 34.0, -96.0, 36.0), None, None) is None
    kept = al.clip_trace(tr, BBox(-98.0, 34.0, -96.0, 36.0), None, None, min_points=1)
    assert kept is not None
    assert len(kept) == 1


def test_extract_traces_picks_only_requested_aircraft(tmp_path):
    parts = _build_parts(tmp_path)
    found = al.extract_traces(parts, {"A03A42", "nothere"})
    assert set(found) == {"a03a42"}
    assert found["a03a42"].type_code == "B738"
    assert len(found["a03a42"]) == 10
