"""Flight preparation: legs, vertical datum, and 1 s interpolation."""

import numpy as np
import pyarrow as pa

from earwitness.aircraft import model as am
from earwitness.aircraft import prep
from earwitness.data.adsb_lol import FLAG_NEW_LEG, POINT_SCHEMA
from earwitness.data.aircraft_types import TypeInfo, TypeTable

T0 = 1.79e9


def _table(rows):
    cols = {name: [] for name in POINT_SCHEMA.names}
    for r in rows:
        for name in POINT_SCHEMA.names:
            cols[name].append(r.get(name))
    cols["time"] = pa.array([int(t * 1e9) for t in cols["time"]], type=pa.timestamp("ns", tz="UTC"))
    arrays = [cols["icao"], cols["time"]] + [
        pa.array(cols[name], type=POINT_SCHEMA.field(name).type) for name in POINT_SCHEMA.names[2:]
    ]
    return pa.table(
        [pa.array(cols["icao"], type=pa.string()), cols["time"], *arrays[2:]], schema=POINT_SCHEMA
    )


def _row(icao, t, lat, lon, alt_baro=5000.0, alt_geom=4900.0, **extra):
    row = {
        "icao": icao,
        "time": t,
        "lat": lat,
        "lon": lon,
        "alt_baro_ft": alt_baro,
        "alt_geom_ft": alt_geom,
        "on_ground": False,
        "gs_kt": 100.0,
        "track_deg": 90.0,
        "vrate_fpm": 0.0,
        "flags": 0,
        "source": "adsb_icao",
        "type_code": "C172",
        "registration": "N1",
        "description": "d",
        "db_flags": 0,
    }
    row.update(extra)
    return row


def test_split_legs_on_flag_gap_and_landing():
    t = np.array([0.0, 5.0, 10.0, 100.0, 105.0, 110.0, 115.0, 120.0])
    flags = np.zeros(8, dtype=np.int16)
    flags[6] = FLAG_NEW_LEG
    on_ground = np.array([False, False, False, False, True, False, False, False])
    legs = prep.split_legs(t, flags, on_ground, gap_s=60.0)
    assert legs == [(0, 3), (3, 5), (5, 6), (6, 8)]
    assert prep.split_legs(np.zeros(0), np.zeros(0, dtype=np.int16), np.zeros(0, dtype=bool)) == []


def test_ellipsoidal_heights_prefer_geometric_then_shift_baro():
    geom = np.array([4900.0, np.nan, np.nan, 4950.0], dtype=np.float32)
    baro = np.array([5000.0, 5010.0, 5020.0, 5050.0], dtype=np.float32)
    h, source = prep.ellipsoidal_heights(geom, baro)
    assert source == "baro_shifted"
    np.testing.assert_allclose(h / 0.3048, [4900.0, 4910.0, 4920.0, 4950.0])  # offset -100 ft
    h2, source2 = prep.ellipsoidal_heights(
        np.full(3, np.nan, dtype=np.float32), np.array([1000.0, 1100.0, np.nan], dtype=np.float32)
    )
    assert source2 == "baro_uncorrected"
    np.testing.assert_allclose(h2 / 0.3048, [1000.0, 1100.0, 1100.0])
    h3, source3 = prep.ellipsoidal_heights(
        np.array([100.0, 200.0], dtype=np.float32), np.array([np.nan, np.nan], dtype=np.float32)
    )
    assert source3 == "geometric"
    assert np.allclose(h3 / 0.3048, [100.0, 200.0])


def test_prepare_flights_groups_classifies_and_drops_ground_points():
    types = TypeTable({"C172": TypeInfo("C172", "L1P"), "B738": TypeInfo("B738", "L2J")})
    rows = [_row("a1", T0 + i * 5.0, 35.0 + 0.001 * i, -97.0) for i in range(8)]
    rows[0]["on_ground"] = True
    rows += [
        _row("a1", T0 + 1000.0 + i * 5.0, 36.0, -97.0 + 0.001 * i) for i in range(6)
    ]  # second leg after a gap
    rows += [_row("b2", T0 + i * 5.0, 35.0, -96.0, type_code="B738") for i in range(6)]
    rows += [
        _row("c3", T0 + i * 5.0, 35.0, -95.0, type_code="XXXX") for i in range(3)
    ]  # too few points
    rows.append(_row("a1", T0 + 5.0, 35.001, -97.0))  # duplicate timestamp
    flights = prep.prepare_flights(_table(rows), types, min_points=5)
    keys = [(f.icao, f.leg, f.engine_class, len(f), f.height_source) for f in flights]
    assert keys == [
        ("a1", 1, "piston", 7, "geometric"),
        ("a1", 2, "piston", 6, "geometric"),
        ("b2", 0, "jet", 6, "geometric"),
    ]
    assert flights[0].is_propeller()
    assert not flights[2].is_propeller()
    assert prep.flights_summary(flights) == {"piston": 2, "jet": 1}
    assert prep.prepare_flights(_table([]), types) == []


def test_interpolate_track_matches_a_straight_flight():
    truth = am.straight_track(
        70.0, 45.0, 450.0, 300.0, t_cpa_s=60.0, t_start=0.0, t_end=120.0, step_s=4.0
    )
    lat, lon, h = truth.to_geodetic(35.0, -97.0, 100.0)
    flight = prep.Flight(
        "x",
        0,
        "C172",
        "",
        "piston",
        T0 + truth.t,
        lat,
        lon,
        h,
        np.full(lat.size, 70.0),
        "geometric",
    )
    track = flight.track(35.0, -97.0, 100.0, step_s=1.0)
    assert track.t.size == 121
    np.testing.assert_allclose(track.position(T0 + 30.0), truth.position(30.0), atol=0.05)
    np.testing.assert_allclose(track.velocity(T0 + 50.0), truth.velocity(50.0), atol=0.05)
    t_cpa, slant = am.closest_approach(track)
    assert abs(t_cpa - (T0 + 60.0)) < 0.05
    assert abs(slant - np.hypot(300.0, 450.0)) < 0.5
