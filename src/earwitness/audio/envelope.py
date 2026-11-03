"""Observed low-frequency loudness envelope for the thunder method.

Decode to mono, resample to 2 kHz, zero-phase band-pass 20 to 250 Hz, RMS over 0.25 s frames,
Gaussian smoothing. The forward model applies the same smoothing to its side, so the two are
compared like with like. Wind noise sits in the same band; `wind_mask` flags it with a heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.signal import butter, sosfiltfilt

from earwitness.audio.decode import resample, to_mono
from earwitness.thunder.model import smooth

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class EnvelopeConfig:
    """Envelope extraction parameters (defaults per video)."""

    rate_hz: int = 2000
    band_hz: tuple[float, float] = (20.0, 250.0)
    filter_order: int = 4
    frame_s: float = 0.25
    smoothing_sigma_s: float = 5.0
    wind_band_hz: tuple[float, float] = (20.0, 60.0)
    wind_ratio: float = 4.0
    """Low-band over mid-band RMS ratio above which a frame looks like wind."""
    wind_min_run_s: float = 3.0
    """Shortest sustained run of such frames that counts as wind."""


@dataclass(frozen=True)
class Envelope:
    """A loudness envelope on a uniform grid of frame centers."""

    t: FloatArray
    """Frame centers, seconds from the start of the recording."""
    values: FloatArray
    frame_s: float


def bandpass(
    samples: ArrayLike, rate: int, band_hz: tuple[float, float], order: int = 4
) -> FloatArray:
    """Zero-phase Butterworth band-pass."""
    lo, hi = band_hz
    hi = min(hi, 0.45 * rate)
    sos = butter(order, [lo, hi], btype="bandpass", fs=rate, output="sos")
    return np.asarray(sosfiltfilt(sos, np.asarray(samples, dtype=float)), dtype=float)


def frame_rms(samples: ArrayLike, rate: int, frame_s: float) -> tuple[FloatArray, FloatArray]:
    """RMS over consecutive non-overlapping frames; returns (frame centers, rms)."""
    x = np.asarray(samples, dtype=float)
    frame_len = max(1, round(frame_s * rate))
    n_frames = x.size // frame_len
    if n_frames == 0:
        raise ValueError("recording shorter than one frame")
    frames = x[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    t = (np.arange(n_frames) + 0.5) * (frame_len / rate)
    return t, rms


def _prepared(samples: ArrayLike, rate: int, cfg: EnvelopeConfig) -> FloatArray:
    x = to_mono(samples)
    if rate != cfg.rate_hz:
        x = resample(x, rate, cfg.rate_hz)
    return x


def observed_envelope(samples: ArrayLike, rate: int, cfg: EnvelopeConfig | None = None) -> Envelope:
    """Low-frequency loudness envelope of a recording."""
    cfg = cfg or EnvelopeConfig()
    x = _prepared(samples, rate, cfg)
    x = bandpass(x, cfg.rate_hz, cfg.band_hz, cfg.filter_order)
    t, rms = frame_rms(x, cfg.rate_hz, cfg.frame_s)
    values = smooth(rms, cfg.frame_s, cfg.smoothing_sigma_s)
    return Envelope(t=t, values=values, frame_s=cfg.frame_s)


def wind_mask(
    samples: ArrayLike, rate: int, cfg: EnvelopeConfig | None = None
) -> NDArray[np.bool_]:
    """Heuristic per-frame wind flag: sustained frames where 20 to 60 Hz dominates 60 to 250 Hz.

    Thunder rumbles have energy across the whole band; wind buffeting piles up at the bottom of
    it for seconds at a time. This is a starting heuristic, not a validated detector.
    """
    cfg = cfg or EnvelopeConfig()
    x = _prepared(samples, rate, cfg)
    lo_band = (cfg.wind_band_hz[0], cfg.wind_band_hz[1])
    hi_band = (cfg.wind_band_hz[1], cfg.band_hz[1])
    _, low = frame_rms(
        bandpass(x, cfg.rate_hz, lo_band, cfg.filter_order), cfg.rate_hz, cfg.frame_s
    )
    _, mid = frame_rms(
        bandpass(x, cfg.rate_hz, hi_band, cfg.filter_order), cfg.rate_hz, cfg.frame_s
    )
    ratio = low / np.maximum(mid, 1e-12)
    flagged = ratio > cfg.wind_ratio
    min_run = max(1, round(cfg.wind_min_run_s / cfg.frame_s))
    out = np.zeros_like(flagged)
    run_start = None
    for i, f in enumerate(np.append(flagged, False)):
        if f and run_start is None:
            run_start = i
        elif not f and run_start is not None:
            if i - run_start >= min_run:
                out[run_start:i] = True
            run_start = None
    return out


def baseline_subtract(values: ArrayLike, percentile: float = 10.0) -> FloatArray:
    """Remove the steady noise floor (a low percentile) so quiet stretches score as silence."""
    v = np.asarray(values, dtype=float)
    if v.size == 0 or percentile <= 0.0:
        return v.copy()
    return np.clip(v - np.percentile(v, percentile), 0.0, None)


def resample_envelope(env: Envelope, step_s: float) -> Envelope:
    """Linear interpolation of an envelope onto a coarser or finer uniform step."""
    t = np.arange(env.t[0], env.t[-1] + 1e-9, step_s)
    return Envelope(t=t, values=np.interp(t, env.t, env.values), frame_s=step_s)
