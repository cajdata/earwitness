"""Sliding cosine scoring, prefilter, bearing weights and model rendering, with worked numbers."""

import numpy as np
import pytest

from earwitness.core import geodesy
from earwitness.data.adapters import LightningEvents
from earwitness.thunder import search as ts
from earwitness.thunder.model import ThunderConfig, kernel_peak_time
from earwitness.thunder.refine import RefineConfig, refine

T0 = 1.8e9


def test_sliding_cosine_worked_numbers():
    np.testing.assert_allclose(
        ts.sliding_cosine([1.0, 0.0], [[0.0, 1.0, 0.0, 0.0]]), [[0.0, 1.0, 0.0]]
    )
    scores = ts.sliding_cosine([1.0, 1.0], [[1.0, 0.0, 1.0, 1.0, 0.0]])
    r = 1.0 / np.sqrt(2.0)
    np.testing.assert_allclose(scores, [[r, r, 1.0, r]], atol=1e-12)


def test_sliding_cosine_matches_brute_force():
    rng = np.random.default_rng(0)
    o = rng.uniform(0.0, 1.0, 7)
    m = rng.uniform(0.0, 1.0, (3, 50))
    m[1, 10:30] = 0.0  # a stretch of zero energy
    scores = ts.sliding_cosine(o, m)
    assert scores.shape == (3, 44)
    for b in range(3):
        for tau in range(44):
            window = m[b, tau : tau + 7]
            expected = (
                0.0
                if np.linalg.norm(window) == 0
                else np.dot(o, window) / (np.linalg.norm(o) * np.linalg.norm(window))
            )
            assert abs(scores[b, tau] - expected) < 1e-9


def test_max_window_count():
    assert ts.max_window_count(np.array([0.0, 1.0, 2.0, 10.0, 11.0]), 2.5) == 3
    assert ts.max_window_count(np.array([0.0, 1.0, 2.0, 10.0, 11.0]), 0.5) == 1
    assert ts.max_window_count(np.array([]), 5.0) == 0


def test_bearing_weights():
    np.testing.assert_allclose(ts.bearing_weights(np.full(10, 45.0), 8, 1.0), np.ones(10))
    bearings = np.array([10.0] * 90 + [190.0] * 10)
    w = ts.bearing_weights(bearings, 8, 1.0)
    assert abs(w.mean() - 1.0) < 1e-12
    assert abs(w[-1] / w[0] - 9.0) < 1e-9
    np.testing.assert_allclose(ts.bearing_weights(bearings, 8, 0.0), np.ones(100))


def _events_around(lat0, lon0, specs):
    lats, lons, times = [], [], []
    for t, bearing, dist in specs:
        la, lo = geodesy.destination(lat0, lon0, bearing, dist)
        lats.append(float(la))
        lons.append(float(lo))
        times.append(T0 + t)
    return LightningEvents(np.array(times), np.array(lats), np.array(lons), source="test")


def test_render_models_single_strike_peaks_at_arrival():
    lat0, lon0 = 35.0, -97.0
    events = _events_around(lat0, lon0, [(100.0, 0.0, 10000.0)])
    index = ts.StrikeIndex(events)
    cfg = ThunderConfig(smoothing_sigma_s=0.0)
    models, n = ts.render_models(index, [lat0], [lon0], T0, 1.0, 200, cfg, ts.SearchConfig())
    assert n.tolist() == [1]
    arrival = 100.0 + np.hypot(10000.0, 2000.0) / 343.0
    decay = cfg.decay_s + cfg.decay_per_km_s * np.hypot(10000.0, 2000.0) / 1000.0
    expected_peak = arrival + float(kernel_peak_time(cfg.rise_s, decay))
    assert abs(np.argmax(models[0]) - expected_peak) <= 1.0
    assert np.all(models[0][: int(arrival)] == 0.0)
    far_models, far_n = ts.render_models(
        index, [lat0 + 1.0], [lon0], T0, 1.0, 200, cfg, ts.SearchConfig()
    )
    assert far_n.tolist() == [0]
    assert np.all(far_models == 0.0)


def test_strike_count_prefilter_counts_windows_within_radius():
    lat0, lon0 = 35.0, -97.0
    events = _events_around(
        lat0,
        lon0,
        [(0.0, 0.0, 5000.0), (10.0, 90.0, 8000.0), (500.0, 180.0, 3000.0), (20.0, 270.0, 40000.0)],
    )
    index = ts.StrikeIndex(events)
    far_lat, far_lon = geodesy.destination(lat0, lon0, 45.0, 200000.0)
    counts = ts.strike_count_prefilter(
        index, np.array([lat0, float(far_lat)]), np.array([lon0, float(far_lon)]), 25000.0, 60.0
    )
    assert counts.tolist() == [2, 0]
    counts_long = ts.strike_count_prefilter(
        index, np.array([lat0]), np.array([lon0]), 25000.0, 1000.0
    )
    assert counts_long.tolist() == [3]


def test_search_scores_true_point_and_offset_highest():
    lat0, lon0 = 35.0, -97.0
    events = _events_around(
        lat0, lon0, [(30.0, 20.0, 6000.0), (90.0, 100.0, 12000.0), (150.0, 250.0, 9000.0)]
    )
    index = ts.StrikeIndex(events)
    cfg = ThunderConfig()
    scfg = ts.SearchConfig(step_s=5.0)
    # The observed envelope is the true point's own model over a 5-minute clip starting at T0.
    observed, _ = ts.render_models(index, [lat0], [lon0], T0, 5.0, 60, cfg, scfg)
    wrong_lat, wrong_lon = geodesy.destination(lat0, lon0, 0.0, 15000.0)
    lat = np.array([lat0, float(wrong_lat)])
    lon = np.array([lon0, float(wrong_lon)])
    res = ts.search(observed[0], index, lat, lon, T0 - 600.0, T0 + 600.0, scfg, cfg, stage="unit")
    assert len(res) == 2
    assert res.stage == "unit"
    assert res.score[0] > 0.999
    assert abs(res.offset_unix[0] - T0) < 1e-6
    assert res.score[0] > res.score[1]
    assert res.top_scores.shape == (2, 3)
    assert res.top_scores[0, 0] >= res.top_scores[0, 1]
    assert res.n_strikes.tolist() == [3, 3]


def test_sliding_pearson_matches_brute_force_and_ignores_a_floor():
    rng = np.random.default_rng(3)
    o = rng.uniform(0.0, 1.0, 9)
    m = rng.uniform(0.0, 1.0, (2, 40))
    scores = ts.sliding_pearson(o, m)
    assert scores.shape == (2, 32)
    for b in range(2):
        for tau in range(32):
            w = m[b, tau : tau + 9]
            expected = np.corrcoef(o, w)[0, 1]
            assert abs(scores[b, tau] - expected) < 1e-9
    # A constant floor added to the model changes cosine but not Pearson.
    lifted = m + 5.0
    np.testing.assert_allclose(ts.sliding_pearson(o, lifted), scores, atol=1e-9)
    assert np.max(np.abs(ts.sliding_cosine(o, lifted) - ts.sliding_cosine(o, m))) > 0.05
    # Flat windows and a flat observation score zero instead of dividing by zero.
    assert np.all(ts.sliding_pearson(o, np.ones((1, 20))) == 0.0)
    assert np.all(ts.sliding_pearson(np.ones(5), m) == 0.0)


def test_sliding_score_dispatch():
    o = np.array([1.0, 0.0])
    m = np.array([[0.0, 1.0, 0.0, 0.0]])
    np.testing.assert_allclose(ts.sliding_score(o, m, "cosine"), ts.sliding_cosine(o, m))
    np.testing.assert_allclose(ts.sliding_score(o, m, "pearson"), ts.sliding_pearson(o, m))
    with pytest.raises(ValueError, match="score mode"):
        ts.sliding_score(o, m, "euclid")


def test_refinement_stays_inside_the_time_prior():
    # the clip truly starts at T0, but a camera clock (read wrongly here) says T0 + 28 to T0 + 32:
    # refinement must not search a minute either side and walk out of the prior to T0
    lat0, lon0 = 35.0, -97.0
    events = _events_around(
        lat0, lon0, [(30.0, 20.0, 6000.0), (90.0, 100.0, 12000.0), (150.0, 250.0, 9000.0)]
    )
    index = ts.StrikeIndex(events)
    cfg = ThunderConfig()
    scfg = ts.SearchConfig(step_s=5.0)
    fine, _ = ts.render_models(index, [lat0], [lon0], T0, 1.0, 300, cfg, ts.SearchConfig())
    lo, hi = T0 + 28.0, T0 + 32.0
    coarse = ts.search(fine[0][::5], index, np.array([lat0]), np.array([lon0]), lo, hi, scfg, cfg)
    assert lo <= coarse.offset_unix[0] <= hi
    picks = np.array([0])
    rc = RefineConfig()
    _, _, free = refine(fine[0], index, coarse, picks, scfg, cfg, rc)
    _, _, kept = refine(fine[0], index, coarse, picks, scfg, cfg, rc, offset_bounds=(lo, hi))
    assert abs(free.offset_unix[0] - T0) <= 1.0  # unbounded, it walks to the true start
    assert lo <= kept.offset_unix[0] <= hi
