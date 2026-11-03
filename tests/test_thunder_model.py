"""Worked numbers for the thunder forward model."""

import numpy as np

from earwitness.core import geodesy
from earwitness.thunder import model as tm


def test_arrival_time_worked_number():
    # 10 km horizontal, 2 km source height, 343 m/s: slant 10198.04 m, 29.732 s.
    assert abs(float(tm.slant_distance(10000.0, 2000.0)) - 10198.039) < 1e-3
    assert abs(float(tm.arrival_time(0.0, 10000.0, 2000.0, 343.0)) - 29.7319) < 1e-3
    assert abs(float(tm.arrival_time(100.0, 10000.0, 2000.0, 343.0)) - 129.7319) < 1e-3


def test_loudness_cap_and_inverse_square():
    assert float(tm.loudness(1000.0)) == 1.0
    assert float(tm.loudness(100.0)) == 1.0  # inside the near field: saturated
    assert abs(float(tm.loudness(2000.0)) - 0.25) < 1e-12
    assert abs(float(tm.loudness(5000.0)) - 0.04) < 1e-12
    assert abs(float(tm.loudness(2000.0, falloff_exponent=1.0)) - 0.5) < 1e-12
    assert abs(float(tm.loudness(4000.0, near_field_m=2000.0)) - 0.25) < 1e-12


def test_pulse_kernel_shape():
    t = np.linspace(0.0, 40.0, 400001)
    k = tm.pulse_kernel(t, 0.5, 3.5)
    assert k[0] == 0.0
    assert abs(k.max() - 1.0) < 1e-6
    t_peak = float(tm.kernel_peak_time(0.5, 3.5))  # 0.5 ln(8) = 1.0397 s
    assert abs(t_peak - 1.0397) < 1e-3
    assert abs(t[np.argmax(k)] - t_peak) < 1e-3
    assert float(tm.pulse_kernel(-1.0, 0.5, 3.5)) == 0.0
    assert k[t > t_peak + 6 * 3.5].max() < 0.01
    assert float(tm.kernel_support(0.5, 3.5)) > t_peak + 6 * 3.5


def test_decay_lengthens_with_distance():
    cfg = tm.ThunderConfig()
    assert abs(float(tm.decay_time(0.0, cfg)) - cfg.decay_s) < 1e-12
    assert float(tm.decay_time(20000.0, cfg)) > float(tm.decay_time(2000.0, cfg))


def test_single_strike_envelope_peaks_after_arrival():
    t = np.arange(0.0, 120.0, 0.25)
    arrival = 29.73
    m = tm.render_envelope(t, [arrival], [1.0], 0.5, 3.5)
    assert m[t < arrival].max() == 0.0
    assert abs(t[np.argmax(m)] - (arrival + 1.0397)) < 0.3
    assert abs(m.max() - 1.0) < 0.02


def test_model_envelope_two_strikes_in_recording_time():
    lat0, lon0 = 35.0, -97.0
    lat1, lon1 = geodesy.destination(lat0, lon0, 0.0, 8000.0)
    lat2, lon2 = geodesy.destination(lat0, lon0, 90.0, 15000.0)
    strikes = tm.StrikeSet(
        time_s=np.array([0.0, 40.0]),
        lat=np.array([float(lat1), float(lat2)]),
        lon=np.array([float(lon1), float(lon2)]),
    )
    cfg = tm.ThunderConfig(smoothing_sigma_s=0.0)
    geom = tm.strike_geometry(strikes, lat0, lon0, cfg)
    expected = [np.hypot(8000.0, 2000.0) / 343.0, 40.0 + np.hypot(15000.0, 2000.0) / 343.0]
    np.testing.assert_allclose(geom.arrival_s, expected, atol=1e-6)
    ratio = geom.amplitude[0] / geom.amplitude[1]
    assert abs(ratio - (geom.slant_m[1] / geom.slant_m[0]) ** 2) < 1e-9

    t = np.arange(0.0, 150.0, 0.25)
    m = tm.model_envelope(t, strikes, lat0, lon0, cfg)
    for arrival, decay in zip(geom.arrival_s, geom.decay_s, strict=True):
        window = (t > arrival) & (t < arrival + 5.0)
        t_peak = arrival + float(tm.kernel_peak_time(cfg.rise_s, decay))
        assert abs(t[window][np.argmax(m[window])] - t_peak) < 0.3
    assert m[t < expected[0]].max() == 0.0


def test_in_cloud_flashes_use_their_own_height():
    cfg = tm.ThunderConfig()
    heights = tm.source_heights(np.array(["CG", "IC", "CG"]), 3, cfg)
    np.testing.assert_allclose(heights, [2000.0, 7000.0, 2000.0])
    np.testing.assert_allclose(tm.source_heights(None, 2, cfg), [2000.0, 2000.0])


def test_smoothing_preserves_area_and_is_identity_at_zero_sigma():
    x = np.zeros(1000)
    x[400:420] = 1.0
    y = tm.smooth(x, 0.25, 5.0)
    assert abs(y.sum() - x.sum()) < 1e-6
    assert y.max() < x.max()
    np.testing.assert_array_equal(tm.smooth(x, 0.25, 0.0), x)
