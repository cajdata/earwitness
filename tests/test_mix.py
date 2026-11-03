"""Noise, filtering and codec round trips behave as labeled."""

import numpy as np
import pytest
from scipy.signal import welch

from earwitness.audio.decode import ffmpeg_path
from earwitness.simulate import mix

needs_ffmpeg = pytest.mark.skipif(ffmpeg_path() is None, reason="ffmpeg not on PATH")


def test_add_noise_hits_the_target_snr():
    rate = 8000
    t = np.arange(10 * rate) / rate
    x = np.sin(2 * np.pi * 100.0 * t)
    rng = np.random.default_rng(0)
    for snr in (-20.0, 0.0, 12.0):
        for color in ("pink", "white"):
            y = mix.add_noise(x, snr, rng, color)
            got = 20 * np.log10(mix.rms(x) / mix.rms(y - x))
            assert abs(got - snr) < 0.05


def test_add_noise_rejects_silence_and_unknown_colors():
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError, match="all-zero"):
        mix.add_noise(np.zeros(100), 0.0, rng)
    with pytest.raises(ValueError, match="unknown noise color"):
        mix.add_noise(np.ones(100), 0.0, rng, "brown")


def test_pink_noise_falls_3_db_per_octave_and_white_is_flat():
    rng = np.random.default_rng(0)
    rate = 16000
    x = mix.pink_noise(1 << 20, rng)
    assert abs(mix.rms(x) - 1.0) < 1e-6
    f, p = welch(x, fs=rate, nperseg=8192)
    lo = p[(f >= 100) & (f < 200)].mean()
    hi = p[(f >= 200) & (f < 400)].mean()
    assert 1.6 < lo / hi < 2.4  # 1/f: twice the density one octave down
    w = mix.white_noise(1 << 18, rng)
    f2, p2 = welch(w, fs=rate, nperseg=4096)
    assert 0.85 < p2[(f2 >= 100) & (f2 < 200)].mean() / p2[(f2 >= 200) & (f2 < 400)].mean() < 1.18


def test_highpass_attenuates_the_low_band_and_passes_the_high_band():
    rate = 8000
    t = np.arange(4 * rate) / rate
    low = np.sin(2 * np.pi * 20.0 * t)
    high = np.sin(2 * np.pi * 500.0 * t)
    y_low = mix.highpass(low, rate, 100.0)[rate:]
    y_high = mix.highpass(high, rate, 100.0)[rate:]
    assert 20 * np.log10(mix.rms(y_low) / mix.rms(low[rate:])) < -20.0
    assert abs(20 * np.log10(mix.rms(y_high) / mix.rms(high[rate:]))) < 1.0


def test_soft_clip_and_normalize_peak():
    x = np.array([-10.0, -1.0, 0.0, 1.0, 10.0])
    y = mix.soft_clip(x, 2.0)
    assert np.all(np.abs(y) < 2.0)
    assert abs(y[3] - 2.0 * np.tanh(0.5)) < 1e-12
    z = mix.normalize_peak(x, 0.5)
    assert abs(np.max(np.abs(z)) - 0.5) < 1e-12
    np.testing.assert_array_equal(mix.normalize_peak(np.zeros(3)), np.zeros(3))


def test_rms_matches_numpy_on_large_arrays():
    rng = np.random.default_rng(2)
    x = rng.standard_normal((1 << 20) + 12345)
    assert abs(mix.rms(x) - np.sqrt(np.mean(x * x))) < 1e-9
    assert mix.rms(np.array([])) == 0.0


@needs_ffmpeg
@pytest.mark.parametrize("codec", ["aac", "mp3"])
def test_codec_roundtrip_preserves_a_tone(codec):
    rate = 16000
    t = np.arange(3 * rate) / rate
    x = 0.5 * np.sin(2 * np.pi * 200.0 * t)
    y = mix.codec_roundtrip(x, rate, codec, 96)
    assert y.shape == x.shape
    seg = slice(rate, 2 * rate)
    xs = x[seg]
    best = 0.0
    for lag in range(-4096, 4097, 4):
        ys = y[seg.start + lag : seg.stop + lag]
        best = max(best, abs(float(np.dot(xs, ys))) / (np.linalg.norm(xs) * np.linalg.norm(ys)))
    assert best > 0.9, f"{codec} round trip decorrelated the tone (best {best:.3f})"


def test_codec_roundtrip_without_ffmpeg_raises(monkeypatch):
    monkeypatch.setattr(mix, "ffmpeg_path", lambda: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        mix.codec_roundtrip(np.zeros(100), 8000, "aac")


def test_codec_roundtrip_rejects_unknown_codec(monkeypatch):
    monkeypatch.setattr(mix, "ffmpeg_path", lambda: "ffmpeg")
    with pytest.raises(ValueError, match="unsupported codec"):
        mix.codec_roundtrip(np.zeros(100), 8000, "opus")
