"""Frequency ratio coverage: frames whose sound the track does not cover score as missing."""

import numpy as np

from earwitness.aircraft import model as am
from earwitness.aircraft.score import ScoreConfig, frequency_ratio

ORIGIN = np.zeros(3)


def test_frequency_ratio_is_nan_outside_the_tracks_emission_coverage():
    # 400 s of track at 50 m/s, closest approach at t=600 some 424 m away; at the first sample
    # the aircraft is 10 km out, so its sound arrives 29 s after the leg starts
    tr = am.straight_track(50.0, 90.0, 300.0, 300.0, 600.0, 400.0, 800.0, 1.0)
    cfg = ScoreConfig()
    t_heard_cpa = 600.0 + np.hypot(300.0, 300.0) / 343.0
    times = np.array([400.0, 420.0, 440.0, t_heard_cpa, 810.0, 850.0])
    ratio = frequency_ratio(tr, ORIGIN, times, cfg)
    assert np.isnan(ratio[0])  # emitted before the first sample
    assert np.isnan(ratio[1])
    assert ratio[2] > 1.1  # approaching at nearly full speed
    assert abs(ratio[3] - 1.0) < 0.005  # the closest-approach emission arrives unshifted
    assert ratio[4] < 0.9  # last samples still arriving, receding
    assert np.isnan(ratio[5])  # emitted after the last sample


def test_distance_weights_follow_one_over_slant_and_keep_gaps():
    from earwitness.aircraft.score import distance_weights, weighted_mean

    slant = np.array([400.0, 800.0, np.nan, 1600.0])
    w = distance_weights(slant, ScoreConfig(distance_weight_power=1.0))
    np.testing.assert_allclose(w[[0, 1, 3]], [1.0, 0.5, 0.25])
    assert np.isnan(w[2])
    flat = distance_weights(slant, ScoreConfig(distance_weight_power=0.0))
    np.testing.assert_allclose(flat[[0, 1, 3]], [1.0, 1.0, 1.0])
    assert np.isnan(flat[2])
    # frames: 10 dB on the ridge near closest approach, 2 dB far out, one unscorable frame
    values = np.array([[10.0, 2.0, 5.0, 2.0], [np.nan, np.nan, np.nan, np.nan]])
    means = weighted_mean(values, w[None, :])
    assert abs(means[0] - (10.0 + 1.0 + 0.5) / 1.75) < 1e-9
    assert np.isnan(means[1])
    assert abs(weighted_mean(values, flat[None, :])[0] - 14.0 / 3.0) < 1e-9


def test_short_legs_are_not_scored_on_their_peak_alone():
    from earwitness.aircraft.score import SpectrogramIndex, score_event
    from earwitness.audio.events import FlyoverEvent, Range
    from earwitness.audio.spectrogram import Spectrogram

    # a spectrogram that is 10 dB bright everywhere: any covered frame scores 10 dB
    t = np.arange(0.0, 200.0, 0.128)
    f = np.arange(0.0, 600.0, 1.95)
    spec = Spectrogram(
        t=t, f=f, power=np.ones((f.size, t.size)), whitened_db=np.full((f.size, t.size), 10.0)
    )
    index = SpectrogramIndex(spec)
    event = FlyoverEvent(
        "e1", Range.around(100.0, 3.0), Range.around(100.0, 3.0), Range(40.0, 60.0),
        Range(200.0, 800.0),
    )  # fmt: skip
    cfg = ScoreConfig()
    # the same flyover, once with a track spanning the whole window and once with a leg that
    # holds only ten seconds around closest approach
    long = am.straight_track(50.0, 90.0, 300.0, 300.0, 100.0, 0.0, 200.0, 1.0)
    short = am.straight_track(50.0, 90.0, 300.0, 300.0, 100.0, 95.0, 105.0, 1.0)
    full = score_event(index, long, ORIGIN, event, 0.0, cfg, None).score
    clipped = score_event(index, short, ORIGIN, event, 0.0, cfg, None).score
    assert abs(full - 10.0) < 1e-6
    assert 0.0 < clipped < 0.4 * full


def test_trace_closeness_ignores_uncovered_points_and_needs_five():
    from earwitness.aircraft.score import trace_closeness
    from earwitness.audio.events import FlyoverEvent, Range

    tr = am.straight_track(50.0, 90.0, 300.0, 300.0, 600.0, 400.0, 800.0, 1.0)
    observer = ORIGIN
    cfg = ScoreConfig()
    t_heard = 600.0 + 300.0 / cfg.speed_of_sound
    t_cov = np.array([t_heard - 20.0, t_heard - 10.0, t_heard, t_heard + 10.0, t_heard + 20.0])
    f_model = 100.0 * frequency_ratio(tr, observer, t_cov, cfg)
    assert np.all(np.isfinite(f_model))
    # five covered points plus three far beyond the track's end: the far ones do not count
    t_all = np.concatenate([t_cov, [1500.0, 1600.0, 1700.0]])
    f_all = np.concatenate([f_model + 1.0, [90.0, 90.0, 90.0]])
    event = FlyoverEvent(
        "e1", Range(t_heard - 3, t_heard + 3), Range(97, 103), Range(40, 60), Range(200, 400),
        trace=np.column_stack([t_all, f_all]),
    )  # fmt: skip
    assert abs(trace_closeness(tr, observer, event, 0.0, 100.0, cfg) + 1.0) < 1e-6
    short = FlyoverEvent(
        "e1", Range(t_heard - 3, t_heard + 3), Range(97, 103), Range(40, 60), Range(200, 400),
        trace=np.column_stack([t_all[:4], f_all[:4]]),
    )  # fmt: skip
    assert np.isnan(trace_closeness(tr, observer, short, 0.0, 100.0, cfg))


def test_at_track_speed_narrows_a_profiled_event():
    from earwitness.aircraft.score import at_track_speed
    from earwitness.audio.events import FlyoverEvent, Range

    tr = am.straight_track(70.0, 90.0, 300.0, 300.0, 600.0, 400.0, 800.0, 1.0)
    valley = np.array([[50.0, 255.0, 112.0, 599.0], [70.0, 500.0, 110.0, 600.0],
                       [100.0, 1020.0, 107.0, 601.0]])  # fmt: skip
    event = FlyoverEvent(
        "e1", Range(590.0, 610.0), Range(100.0, 120.0), Range(40.0, 110.0), Range(100.0, 1600.0),
        valley=valley,
    )  # fmt: skip
    narrowed = at_track_speed(event, tr, ORIGIN)
    np.testing.assert_allclose([narrowed.f0_hz.lo, narrowed.f0_hz.hi], [107.0, 113.0])
    np.testing.assert_allclose([narrowed.t0_s.lo, narrowed.t0_s.hi], [597.0, 603.0])
    np.testing.assert_allclose([narrowed.d_m.lo, narrowed.d_m.hi], [250.0, 750.0])
    plain = FlyoverEvent(
        "e1", Range(590.0, 610.0), Range(100.0, 120.0), Range(40.0, 110.0), Range(100.0, 1600.0)
    )
    assert at_track_speed(plain, tr, ORIGIN) is plain


def test_known_time_sweep_uses_the_narrowed_width_not_the_envelope():
    from earwitness.aircraft.score import time_sweep_for
    from earwitness.audio.events import FlyoverEvent, Range

    cfg = ScoreConfig()
    wide = FlyoverEvent(
        "e1", Range(700.0, 762.0), Range(80.0, 90.0), Range(30.0, 200.0), Range(100.0, 5000.0)
    )
    narrowed = FlyoverEvent(
        "e1", Range(720.0, 726.0), Range(80.0, 90.0), Range(30.0, 200.0), Range(100.0, 5000.0)
    )
    assert time_sweep_for(wide, cfg, known_time_tolerance_s=2.0) == 33.0  # what it must not be
    assert time_sweep_for(narrowed, cfg, known_time_tolerance_s=2.0) == 5.0
    assert time_sweep_for(narrowed, cfg) == 3.0  # two-event default, at least cfg.time_sweep_s
    assert time_sweep_for(wide, cfg) == 31.0
    assert time_sweep_for(wide, cfg, time_sweep_s=4.0) == 4.0


def test_at_track_speed_uses_the_pass_heard_not_the_nearest():
    import sys
    from pathlib import Path

    from earwitness.aircraft.score import at_track_speed
    from earwitness.audio.events import FlyoverEvent, Range

    sys.path.insert(0, str(Path(__file__).parent))
    from test_aircraft_model import _two_pass_track

    tr = _two_pass_track()  # 40 m/s pass heard at 302 s, a nearer 90 m/s pass later
    valley = np.array([[40.0, 671.0, 110.0, 302.0], [90.0, 3400.0, 104.0, 312.0]])
    event = FlyoverEvent(
        "e1", Range(296.0, 318.0), Range(98.0, 116.0), Range(28.0, 117.0), Range(300.0, 5100.0),
        valley=valley,
    )  # fmt: skip
    heard = at_track_speed(event, tr, ORIGIN, t_start=0.0)
    np.testing.assert_allclose([heard.f0_hz.lo, heard.f0_hz.hi], [107.0, 113.0], atol=0.01)
    np.testing.assert_allclose([heard.t0_s.lo, heard.t0_s.hi], [299.0, 305.0], atol=0.01)
    # without the recording start, the nearest pass of the whole track decides
    nearest = at_track_speed(event, tr, ORIGIN)
    np.testing.assert_allclose([nearest.f0_hz.lo, nearest.f0_hz.hi], [101.0, 107.0], atol=0.01)
    # an empty profile is no profile
    empty = FlyoverEvent(
        "e1", Range(296.0, 318.0), Range(98.0, 116.0), Range(28.0, 117.0), Range(300.0, 5100.0),
        valley=np.zeros((0, 4)),
    )  # fmt: skip
    assert not empty.profiled
    assert at_track_speed(empty, tr, ORIGIN, t_start=0.0) is empty


def test_the_trace_window_option_scores_only_where_the_line_was_traced():
    from earwitness.aircraft.score import SpectrogramIndex, event_window, score_event
    from earwitness.audio.events import FlyoverEvent, Range
    from earwitness.audio.spectrogram import Spectrogram

    # noise everywhere and a faint line, 6 dB, along the true curve for only 20 s around the
    # heard closest approach, the way a distant turboprop shows in a phone recording
    rng = np.random.default_rng(7)
    t = np.arange(0.0, 400.0, 0.128)
    f = np.arange(0.0, 600.0, 1.0)
    db = rng.normal(0.0, 2.0, size=(f.size, t.size))
    tr = am.straight_track(90.0, 90.0, 800.0, 1500.0, 200.0, 0.0, 400.0, 1.0)
    cfg = ScoreConfig()
    heard = 200.0 + float(np.hypot(800.0, 1500.0)) / cfg.speed_of_sound
    visible = np.flatnonzero((t >= heard - 10.0) & (t <= heard + 10.0))
    line = 150.0 * frequency_ratio(tr, ORIGIN, t[visible], cfg)
    for k in (0, 1):  # two bins wide, so the linear interpolation sees 6 dB all along it
        db[np.floor(line).astype(int) + k, visible] += 6.0
    index = SpectrogramIndex(Spectrogram(t=t, f=f, power=np.ones_like(db), whitened_db=db))
    cfg = ScoreConfig(trace_window=True)  # an option; the fixed window is the default
    ranges = (Range.around(heard, 3.0), Range.around(150.0, 3.0), Range(70, 110), Range(900, 2600))
    traced = FlyoverEvent("e2", *ranges, trace=np.column_stack([t[visible], line]))
    plain = FlyoverEvent("e2", *ranges)
    assert event_window(plain, cfg) == (heard - 60.0, heard + 60.0)
    assert event_window(traced, ScoreConfig()) == (heard - 60.0, heard + 60.0)
    lo, hi = event_window(traced, cfg)
    assert abs(lo - (t[visible][0] - 10.0)) < 1e-9
    assert abs(hi - (t[visible][-1] + 10.0)) < 1e-9
    whole = score_event(index, tr, ORIGIN, traced, 0.0, ScoreConfig(trace_window=False), None)
    seen = score_event(index, tr, ORIGIN, traced, 0.0, cfg, None)
    # worked numbers: with weights d / slant the visible 20 s carry 19.2 of the 70.7 weighted
    # seconds of the 120 s window and 19.2 of 34.8 of the trace window, so the line's 6 dB
    # averages to about 1.6 dB and 3.3 dB
    assert 1.2 < whole.score < 2.3
    assert 2.8 < seen.score < 4.0
    assert whole.frames > 2.5 * seen.frames
    # a point 1.5 km off sees a different curve and stays well below the site either way
    off = geodesy_offset(1500.0)
    assert score_event(index, tr, off, traced, 0.0, cfg, None).score < 0.5 * seen.score


def geodesy_offset(east_m: float) -> np.ndarray:
    """An observer `east_m` east of the origin in the tracks' local frame."""
    return np.array([east_m, 0.0, 0.0])
