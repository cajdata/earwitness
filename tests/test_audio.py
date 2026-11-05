"""Decoding, envelopes and spectrograms on signals with known content."""

import numpy as np

from earwitness.audio import decode, envelope
from earwitness.audio import spectrogram as sg


def test_wav_roundtrip(tmp_path):
    rate = 8000
    t = np.arange(rate) / rate
    x = 0.4 * np.sin(2 * np.pi * 100.0 * t)
    path = decode.write_wav(tmp_path / "a.wav", x, rate)
    y, r = decode.read_audio(path)
    assert r == rate
    assert y.shape == x.shape
    assert np.max(np.abs(y - x)) < 1e-3  # 16-bit quantization


def test_read_audio_resamples_on_request(tmp_path):
    rate = 8000
    x = np.zeros(rate)
    decode.write_wav(tmp_path / "z.wav", x, rate)
    y, r = decode.read_audio(tmp_path / "z.wav", target_rate=2000)
    assert r == 2000
    assert y.size == 2000


def test_resample_changes_length_and_keeps_the_tone():
    rate = 8000
    t = np.arange(2 * rate) / rate
    x = np.sin(2 * np.pi * 100.0 * t)
    y = decode.resample(x, rate, 2000)
    assert y.size == 4000
    core = y[200:-200]
    crossings = np.sum(np.diff(np.sign(core)) != 0)
    assert abs(crossings / (core.size / 2000) - 200.0) < 4.0


def test_to_mono_averages_channels():
    stereo = np.array([[1.0, 3.0], [2.0, 4.0]])
    np.testing.assert_allclose(decode.to_mono(stereo), [2.0, 3.0])
    np.testing.assert_allclose(decode.to_mono([1.0, 2.0]), [1.0, 2.0])


def test_envelope_of_a_burst_peaks_at_the_burst():
    rate = 4000
    rng = np.random.default_rng(0)
    x = 0.01 * rng.standard_normal(30 * rate)
    i0, i1 = 10 * rate, 12 * rate
    x[i0:i1] += rng.standard_normal(i1 - i0)
    env = envelope.observed_envelope(x, rate, envelope.EnvelopeConfig(smoothing_sigma_s=0.5))
    assert env.frame_s == 0.25
    assert abs((env.t[1] - env.t[0]) - 0.25) < 1e-12
    peak_t = env.t[np.argmax(env.values)]
    assert 10.0 <= peak_t <= 12.0
    assert env.values[env.t < 9.0].max() < 0.2 * env.values.max()
    coarse = envelope.resample_envelope(env, 1.0)
    assert abs((coarse.t[1] - coarse.t[0]) - 1.0) < 1e-12


def test_spectrogram_ridge_finds_a_pure_tone_in_raw_power():
    rate = 4000
    rng = np.random.default_rng(1)
    t = np.arange(20 * rate) / rate
    x = np.sin(2 * np.pi * 123.4 * t) + 0.5 * rng.standard_normal(t.size)
    spec = sg.spectrogram(x, rate, nfft=4096, hop=1024, fmax=500.0)
    assert spec.f[-1] <= 500.0
    assert spec.whitened_db.shape == (spec.f.size, spec.t.size)
    tt, f_peak = sg.ridge(spec, 100.0, 150.0, whitened=False)
    mid = (tt > 2.0) & (tt < 18.0)
    assert np.max(np.abs(f_peak[mid] - 123.4)) < 0.3


def test_whitening_removes_a_stationary_tone_but_keeps_a_sweep():
    rate = 4000
    rng = np.random.default_rng(4)
    t = np.arange(40 * rate) / rate
    hum = np.sin(2 * np.pi * 60.0 * t)
    f_inst = 110.0 + 1.5 * t  # linear chirp 110 -> 170 Hz
    sweep = 0.3 * np.sin(2 * np.pi * np.cumsum(f_inst) / rate)
    x = hum + sweep + 0.5 * rng.standard_normal(t.size)
    spec = sg.spectrogram(x, rate, nfft=4096, hop=1024, fmax=500.0)
    hum_bin = int(np.argmin(np.abs(spec.f - 60.0)))
    assert np.median(spec.whitened_db[hum_bin]) < 1.0  # the hum is flattened to ~0 dB
    tt, f_peak = sg.ridge(spec, 100.0, 180.0)
    mid = (tt > 2.0) & (tt < 38.0)
    assert np.max(np.abs(f_peak[mid] - (110.0 + 1.5 * tt[mid]))) < 0.6


def _line_spectrogram(pad_s: float, seed: int = 3) -> tuple[sg.Spectrogram, float]:
    """Whitened noise with a loud falling line, 20 dB, from 10 s to 50 s of a 60 s view that
    starts `pad_s` into the recording; the recording continues `pad_s` past the view."""
    rng = np.random.default_rng(seed)
    dt = 0.128
    t = np.arange(0.0, 60.0 + 2.0 * pad_s, dt)
    f = np.arange(0.0, 200.0, 1.0)
    db = rng.normal(0.0, 1.5, size=(f.size, t.size))
    on = (t >= pad_s + 10.0) & (t <= pad_s + 50.0)
    line_f = 140.0 - 0.8 * (t[on] - pad_s - 10.0)  # 140 Hz falling to 108 Hz
    db[np.rint(line_f).astype(int), np.flatnonzero(on)] += 20.0
    return sg.Spectrogram(t=t, f=f, power=np.ones_like(db), whitened_db=db), pad_s


def test_trace_line_keeps_a_loud_line_that_fills_most_of_the_view():
    spec, pad = _line_spectrogram(0.0)
    t, f, db = sg.trace_line(spec, 100.0, 150.0, pad, pad + 60.0)
    # the line holds 40 s of the 60 s view at 0.128 s frames: 313 frames, nearly all traced
    assert t.size >= 300
    assert t.min() <= pad + 10.5
    assert t.max() >= pad + 49.5
    off = np.abs(f - (140.0 - 0.8 * (t - pad - 10.0)))
    assert np.mean(off <= 1.0) >= 0.98  # an edge frame or two may come from the noise
    assert np.mean(db >= 15.0) >= 0.98


def test_trace_line_does_not_depend_on_the_recording_outside_the_view():
    # the same view alone and inside a recording nine times longer, with the same noise in view
    short, _ = _line_spectrogram(0.0)
    long, pad = _line_spectrogram(2110 * 0.128)  # a whole number of frames
    in_view = (long.t >= pad) & (long.t <= pad + 60.0)
    long.whitened_db[:, in_view] = short.whitened_db[:, : int(in_view.sum())]
    a = sg.trace_line(short, 100.0, 150.0, 0.0, 60.0)
    b = sg.trace_line(long, 100.0, 150.0, pad, pad + 60.0)
    assert a[0].size == b[0].size
    np.testing.assert_allclose(a[0], b[0] - pad, atol=1e-9)
    np.testing.assert_allclose(a[1], b[1], atol=1e-9)
