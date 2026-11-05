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


def follow_ridge(
    t: FloatArray,
    f: FloatArray,
    bright: FloatArray,
    allowed: NDArray[np.bool_],
    max_step: float,
    max_gap_s: float,
) -> NDArray[np.bool_]:
    """Frames that belong to the one line through the brightest allowed frame.

    Starting at the brightest allowed frame, walk outward in both directions, accepting a frame
    when it is allowed and its frequency is within `max_step` (a fraction) of the last accepted
    one, and stopping once the line has been missing for more than `max_gap_s`.
    """
    out = np.zeros(t.size, dtype=bool)
    candidates = np.flatnonzero(allowed)
    seed = int(candidates[np.argmax(bright[candidates])])
    out[seed] = True
    for direction in (1, -1):
        last_f = float(f[seed])
        last_t = float(t[seed])
        i = seed + direction
        while 0 <= i < t.size:
            if abs(float(t[i]) - last_t) > max_gap_s:
                break
            if allowed[i] and abs(float(f[i]) - last_f) <= max_step * last_f:
                out[i] = True
                last_f = float(f[i])
                last_t = float(t[i])
            i += direction
    return out


NOISE_PERCENTILE = 25.0
"""Per-frame brightness percentile of the box taken as its noise level when tracing a line."""


def trace_line(
    spec: Spectrogram,
    f_lo: float,
    f_hi: float,
    t_lo: float | None = None,
    t_hi: float | None = None,
    min_db: float = 3.0,
    whitened: bool = True,
    follow: bool = True,
    max_step: float = 0.04,
    max_gap_s: float = 1.5,
    min_margin_db: float = 2.0,
    noise_percentile: float = NOISE_PERCENTILE,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """The one line in a band and time box, the way the UI's auto-trace finds it: (t, f, dB).

    Each frame's brightest bin in the band (`ridge`), frames at least `min_db` bright, then,
    when following, frames whose 2 s average stands `min_margin_db` above the box's noise level
    and that join the line through its brightest frame (`follow_ridge`). The noise level is the
    `noise_percentile` of the box's per-frame brightness, so it depends only on what is in view:
    a quarter of the frames in a sensible view lie off the line. (A median over the box's bright
    frames and every dim frame of the whole recording made the same view trace differently in a
    clip and in the full file, and a loud line filling most of the view raised it to the line
    itself, so only a few seconds of the line passed.)
    """
    t, f = ridge(spec, f_lo, f_hi, whitened=whitened)
    rows = (spec.f >= f_lo) & (spec.f <= f_hi)
    view = spec.whitened_db if whitened else 10.0 * np.log10(spec.power + 1e-30)
    bright = view[rows].max(axis=0)
    box = np.ones(t.size, dtype=bool)
    if t_lo is not None:
        box &= t >= t_lo
    if t_hi is not None:
        box &= t <= t_hi
    keep = box & (bright >= min_db)
    if follow and keep.any():
        dt = float(t[1] - t[0]) if t.size > 1 else 1.0
        width = max(1, round(2.0 / dt))
        smooth = np.convolve(bright, np.ones(width) / width, mode="same")
        noise = float(np.percentile(bright[box], noise_percentile))
        keep &= smooth >= noise + min_margin_db
        if keep.any():
            keep = follow_ridge(t, f, bright, keep, max_step, max_gap_s)
    return t[keep], f[keep], bright[keep]
