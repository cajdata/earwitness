"""Whitened low-band spectrogram for the aircraft method.

Long-window STFT for frequency resolution (video 3 used 8192-sample Hamming), limited to the
band where propeller harmonics live, with per-frequency median whitening so faint sweeping lines
stand out against stationary noise and hum.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.signal import ShortTimeFFT
from scipy.signal.windows import get_window

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class Spectrogram:
    """Power spectrogram restricted to [0, fmax] with its whitened dB view."""

    t: FloatArray
    """Frame centers, s."""
    f: FloatArray
    """Bin frequencies, Hz."""
    power: FloatArray
    """Power spectral density, shape (f, t)."""
    whitened_db: FloatArray
    """10 log10 of power divided by its per-frequency median over time, shape (f, t)."""


def spectrogram(
    samples: ArrayLike,
    rate: int,
    nfft: int = 8192,
    hop: int | None = None,
    fmax: float = 500.0,
    window: str = "hamming",
    whiten_percentile: float = 50.0,
) -> Spectrogram:
    """Compute the whitened spectrogram of a mono signal.

    Whitening divides each frequency row by its `whiten_percentile` over time (the median per the
    handoff). A tone that lingers near one frequency for a large share of the clip, such as a far
    approach asymptote, raises that row's median and is partly whitened away; a lower percentile
    is more tolerant of that.
    """
    x = np.asarray(samples, dtype=float)
    hop = hop or nfft // 4
    win = get_window(window, nfft, fftbins=True)
    sft = ShortTimeFFT(win, hop=hop, fs=rate, mfft=nfft, scale_to="psd")
    spec = sft.stft(x)
    power = np.abs(spec) ** 2
    keep = sft.f <= fmax
    power = power[keep]
    floor = np.percentile(power, whiten_percentile, axis=1, keepdims=True)
    whitened = power / np.maximum(floor, np.finfo(float).tiny)
    whitened_db = 10.0 * np.log10(whitened + 1e-12)
    return Spectrogram(t=sft.t(x.size), f=sft.f[keep], power=power, whitened_db=whitened_db)


def ridge(
    spec: Spectrogram, f_lo: float, f_hi: float, whitened: bool = True
) -> tuple[FloatArray, FloatArray]:
    """Per-frame peak frequency inside a band, with parabolic sub-bin interpolation.

    Returns (frame times, peak frequencies). Frames where the band holds no energy still return
    their argmax; callers mask by time or by brightness. The whitened view suits sweeping Doppler
    lines; a tone that sits still is whitened away, so pass `whitened=False` for those.
    """
    idx = np.flatnonzero((spec.f >= f_lo) & (spec.f <= f_hi))
    if idx.size < 3:
        raise ValueError("band must span at least three frequency bins")
    sub = spec.whitened_db[idx] if whitened else 10.0 * np.log10(spec.power[idx] + 1e-30)
    k = np.argmax(sub, axis=0)
    k = np.clip(k, 1, idx.size - 2)
    cols = np.arange(sub.shape[1])
    y0, y1, y2 = sub[k - 1, cols], sub[k, cols], sub[k + 1, cols]
    denom = y0 - 2.0 * y1 + y2
    delta = np.where(np.abs(denom) > 1e-12, 0.5 * (y0 - y2) / np.where(denom == 0, 1, denom), 0.0)
    df = float(spec.f[1] - spec.f[0])
    return spec.t, spec.f[idx[k]] + np.clip(delta, -1.0, 1.0) * df
