"""Lattice, closest approach to many points, distance tubes, pairing, and the silence filter."""

import numpy as np

from earwitness.aircraft import funnel as fn
from earwitness.aircraft import model as am
from earwitness.aircraft.prep import Flight
from earwitness.audio.events import EventSet, FlyoverEvent, Range, SilenceWindow
from earwitness.core import geodesy
from earwitness.core.grid import BBox

T0 = 1.79e9
SITE = (35.0, -97.0)


def _flight(icao, speed, heading, height, lateral, t_cpa, span=600.0, step=4.0, klass="piston"):
    tr = am.straight_track(speed, heading, height, lateral, t_cpa, t_cpa - span, t_cpa + span, step)
    lat, lon, h = tr.to_geodetic(*SITE, 0.0)
    return Flight(
        icao, 0, "C172", "", klass, T0 + tr.t, lat, lon, h, np.full(lat.size, speed), "geometric"
    )


def _event(ident, t0, f0, v, d):
    return FlyoverEvent(
        ident,
        Range.around(t0, 3.0),
        Range.around(f0, 3.0),
        Range(v * 0.85, v * 1.15),
        Range(d * 0.5, d * 1.5),
    )


def test_lattice_roundtrip_and_spacing():
    box = BBox.around(*SITE, 20000.0)
    lat = np.array([35.0, 35.01, 34.99])
    lon = np.array([-97.0, -97.02, -96.98])
    lattice = fn.Lattice(box, 200.0)
    ids = lattice.cell(lat, lon)
    assert ids.size == 3
    assert np.unique(ids).size == 3
    clat, clon = lattice.center(ids)
    assert np.all(geodesy.horizontal_distance(clat, clon, lat[0], lon[0])[:1] < 150.0)
    assert np.array_equal(lattice.cell(clat, clon), ids)
    # neighbors east and north are one spacing away
    east = lattice.center(ids[:1] + 1)
    north = lattice.center(ids[:1] + fn.COLS)
    east = (float(east[0][0]), float(east[1][0]))
    north = (float(north[0][0]), float(north[1][0]))
    assert abs(float(geodesy.horizontal_distance(east[0], east[1], clat[0], clon[0])) - 200.0) < 1.0
    assert (
        abs(float(geodesy.horizontal_distance(north[0], north[1], clat[0], clon[0])) - 200.0) < 1.0
    )


def test_cpa_to_points_matches_closest_approach():
    tr = am.straight_track(80.0, 30.0, 400.0, 250.0, 50.0, 0.0, 100.0, 1.0)
    pts = np.array([[0.0, 0.0, 0.0], [500.0, -200.0, 0.0], [-3000.0, 1000.0, 0.0]])
    t_cpa, slant = fn.cpa_to_points(tr.enu, tr.t, pts)
    for k in range(3):
        t_ref, d_ref = am.closest_approach(tr, pts[k])
        assert abs(t_cpa[k] - t_ref) < 2e-3
        assert abs(slant[k] - d_ref) < 0.05


def _flight_set(flights, cfg):
    box = BBox.around(*SITE, 20000.0)
    return fn.FlightSet.build(flights, box, cfg), fn.Lattice(box, cfg.grid_spacing_m)


def test_tube_cells_cover_the_distance_band():
    cfg = fn.FunnelConfig()
    f = _flight("aaa111", 70.0, 90.0, 400.0, 300.0, 600.0)
    fs, lattice = _flight_set([f], cfg)
    event = _event(
        "e1", 600.0, 110.0, 70.0, 500.0
    )  # d range 250..750 m, height 400 -> lateral 0..634 m
    tube = fn.tube_cells(fs, 0, event, lattice, cfg)
    assert tube.ids.size > 50
    assert np.all(tube.slant >= event.d_m.lo - 1e-6)
    assert np.all(tube.slant <= event.d_m.hi + 1e-6)
    assert np.unique(tube.ids).size == tube.ids.size
    # the site itself (lateral 300 m, slant 500 m) must be in the tube, with the right timing
    site_cell = lattice.cell(np.array([SITE[0]]), np.array([SITE[1]]))[0]
    assert site_cell in set(tube.ids.tolist())
    k = int(np.flatnonzero(tube.ids == site_cell)[0])
    assert abs(tube.heard[k] - (T0 + 600.0 + 500.0 / 343.0)) < 1.5
    # a speed range the flight does not meet yields nothing
    slow = FlyoverEvent("x", event.t0_s, event.f0_hz, Range(20.0, 40.0), event.d_m)
    assert fn.tube_cells(fs, 0, slow, lattice, cfg).ids.size == 0


def test_two_event_candidates_find_the_site_and_the_right_pair():
    cfg = fn.FunnelConfig()
    f1 = _flight("aaa111", 70.0, 90.0, 400.0, 300.0, 600.0)
    f2 = _flight("bbb222", 85.0, 200.0, 500.0, -250.0, 840.0)
    decoy_far = _flight("ccc333", 70.0, 45.0, 400.0, 9000.0, 700.0)  # passes 9 km away
    decoy_jet = _flight("ddd444", 72.0, 10.0, 400.0, 280.0, 605.0, klass="jet")
    fs, lattice = _flight_set([f1, f2, decoy_far, decoy_jet], cfg)
    assert len(fs) == 3  # the jet is not admitted
    e1 = _event("e1", 600.0 + 500.0 / 343.0, 110.0, 70.0, 500.0)
    e2 = _event("e2", 840.0 + np.hypot(250.0, 500.0) / 343.0, 95.0, 85.0, np.hypot(250.0, 500.0))
    gap = EventSet([e1, e2]).gap()
    cand, n_pairs = fn.two_event_candidates(fs, e1, e2, gap, lattice, cfg)
    assert n_pairs >= 1
    assert len(cand) > 0
    assert set(zip(cand.f1.tolist(), cand.f2.tolist(), strict=True)) == {(0, 1)}
    d = geodesy.horizontal_distance(cand.lat, cand.lon, *SITE)
    assert d.min() < 150.0
    assert np.all(np.abs(cand.t_start - T0) < 10.0)


def test_two_event_candidates_search_every_meeting_place():
    cfg = fn.FunnelConfig()
    # a leader and a follower on the same eastbound line 500 s apart meet, in the gap-shifted
    # sense, everywhere along it; the site is beside the line far from where the tracks start
    lead = _flight("aaa111", 70.0, 90.0, 400.0, 300.0, 400.0, span=400.0)
    follow = _flight("bbb222", 70.0, 90.0, 400.0, 300.0, 900.0, span=400.0)
    fs, lattice = _flight_set([lead, follow], cfg)
    slant = float(np.hypot(300.0, 400.0))
    e1 = _event("e1", 400.0 + slant / 343.0, 110.0, 70.0, slant)
    e2 = _event("e2", 900.0 + slant / 343.0, 95.0, 70.0, slant)
    gap = EventSet([e1, e2]).gap()
    hints = fn.pair_prefilter(fs, e1, e2, gap, cfg)
    assert len(hints) > 5  # one per meeting place along the line, not one per pair
    cand, _ = fn.two_event_candidates(fs, e1, e2, gap, lattice, cfg)
    assert len(cand) > 0
    d = geodesy.horizontal_distance(cand.lat, cand.lon, *SITE)
    assert d.min() < 150.0
    # cells elsewhere along the line are legitimate too, with their own implied start times;
    # the ones beside the site imply the true start
    assert np.all(np.abs(cand.t_start[d < 150.0] - T0) < 10.0)


def test_single_event_candidates_use_the_known_time():
    cfg = fn.FunnelConfig()
    f1 = _flight("aaa111", 70.0, 90.0, 400.0, 300.0, 600.0)
    other = _flight("bbb222", 70.0, 90.0, 400.0, 300.0, 3000.0)  # same geometry, 40 min later
    fs, lattice = _flight_set([f1, other], cfg)
    e1 = _event("e1", 600.0 + 500.0 / 343.0, 110.0, 70.0, 500.0)
    cand = fn.single_event_candidates(fs, e1, T0, 1.0, lattice, cfg)
    assert len(cand) > 0
    assert set(cand.f1.tolist()) == {0}
    assert geodesy.horizontal_distance(cand.lat, cand.lon, *SITE).min() < 150.0
    assert np.all(cand.f2 == -1)


def test_silence_hits_count_other_flights_in_silent_windows():
    cfg = fn.FunnelConfig()
    f1 = _flight("aaa111", 70.0, 90.0, 400.0, 300.0, 600.0)
    intruder = _flight("bbb222", 70.0, 0.0, 400.0, 1000.0, 200.0)  # 1 km from the site at t=200
    fs, _ = _flight_set([f1, intruder], cfg)
    cand = fn.Candidates(
        cell=np.zeros(1, np.int64), lat=np.array([SITE[0]]), lon=np.array([SITE[1]]),
        f1=np.zeros(1, np.int64), f2=np.full(1, -1), heard1=np.zeros(1), heard2=np.zeros(1),
        slant1=np.zeros(1), slant2=np.zeros(1), t_start=np.array([T0]),
    )  # fmt: skip
    assert fn.silence_hits(fs, cand, [SilenceWindow(100.0, 300.0)], cfg).tolist() == [1]
    assert fn.silence_hits(fs, cand, [SilenceWindow(900.0, 1100.0)], cfg).tolist() == [0]
    assert fn.silence_hits(fs, cand, [], cfg).tolist() == [0]


def _site_candidate():
    return fn.Candidates(
        cell=np.zeros(1, np.int64), lat=np.array([SITE[0]]), lon=np.array([SITE[1]]),
        f1=np.zeros(1, np.int64), f2=np.full(1, -1), heard1=np.zeros(1), heard2=np.zeros(1),
        slant1=np.zeros(1), slant2=np.zeros(1), t_start=np.array([T0]),
    )  # fmt: skip


def test_silence_hits_use_slant_range_not_ground_distance():
    cfg = fn.FunnelConfig()
    f1 = _flight("aaa111", 70.0, 90.0, 400.0, 300.0, 600.0)
    # 1 km beside the site but 4.5 km up: 4.6 km slant, out of earshot
    high = _flight("ccc333", 70.0, 0.0, 4500.0, 1000.0, 200.0)
    fs, _ = _flight_set([f1, high], cfg)
    cand = _site_candidate()
    assert fn.silence_hits(fs, cand, [SilenceWindow(100.0, 300.0)], cfg).tolist() == [0]
    # a candidate standing on a 4.2 km plateau would hear it
    hits = fn.silence_hits(fs, cand, [SilenceWindow(100.0, 300.0)], cfg, np.array([4200.0]))
    assert hits.tolist() == [1]


def test_silence_hits_compare_arrival_times():
    cfg = fn.FunnelConfig()
    f1 = _flight("aaa111", 70.0, 90.0, 400.0, 300.0, 600.0)
    # closest approach 3.9 km north at t=290: inside 4 km only for about 25 s around it, and its
    # sound arrives 11 s late, so a window ending at 285 is silent in recording time
    grazing = _flight("ddd444", 70.0, 90.0, 0.0, 3900.0, 290.0)
    fs, _ = _flight_set([f1, grazing], cfg)
    cand = _site_candidate()
    assert fn.silence_hits(fs, cand, [SilenceWindow(100.0, 285.0)], cfg).tolist() == [0]
    assert fn.silence_hits(fs, cand, [SilenceWindow(100.0, 300.0)], cfg).tolist() == [1]


def test_cpa_to_points_does_not_bridge_gaps_in_a_subsetted_track():
    tr = am.straight_track(80.0, 30.0, 400.0, 250.0, 50.0, 0.0, 100.0, 1.0)
    mask = (tr.t <= 40.0) | (tr.t >= 60.0)
    pts = np.array([[0.0, 0.0, 0.0], [-2400.0, -4157.0, 0.0]])  # closest at t=50 (gap) and t=20
    t_cpa, slant = fn.cpa_to_points(tr.enu[mask], tr.t[mask], pts)
    # inside the gap the nearest kept sample answers, with no parabola fitted across 20 s
    assert t_cpa[0] in (40.0, 60.0)
    assert abs(slant[0] - np.linalg.norm(tr.position(t_cpa[0]))) < 1e-6
    t_ref, d_ref = am.closest_approach(tr, pts[1])
    assert abs(t_cpa[1] - t_ref) < 2e-3
    assert abs(slant[1] - d_ref) < 0.05


def test_tube_cells_near_a_hint_agree_with_the_full_tube():
    cfg = fn.FunnelConfig()
    f = _flight("aaa111", 70.0, 90.0, 400.0, 300.0, 600.0)  # 84 km of track
    fs, lattice = _flight_set([f], cfg)
    event = _event("e1", 600.0, 110.0, 70.0, 500.0)
    full = fn.tube_cells(fs, 0, event, lattice, cfg)
    hint = fs.xy(np.array([SITE[0]]), np.array([SITE[1]]))[0]
    near = fn.tube_cells(fs, 0, event, lattice, cfg, None, hint, 2000.0)
    assert 0 < near.ids.size < full.ids.size
    site_cell = lattice.cell(np.array([SITE[0]]), np.array([SITE[1]]))[0]
    assert site_cell in set(near.ids.tolist())
    # the two tubes sample the band from different starting points, so a few edge cells differ;
    # every shared cell must carry the same closest approach
    common, i_full, i_near = np.intersect1d(full.ids, near.ids, return_indices=True)
    assert common.size >= 0.9 * near.ids.size
    np.testing.assert_allclose(near.heard[i_near], full.heard[i_full], atol=1e-6)
    np.testing.assert_allclose(near.slant[i_near], full.slant[i_full], atol=1e-6)
