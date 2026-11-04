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
