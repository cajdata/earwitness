"""Observed low-frequency loudness envelope for the thunder method.

Decode to mono, resample to 2 kHz, zero-phase band-pass 20 to 250 Hz, RMS over 0.25 s frames,
Gaussian smoothing. The forward model applies the same smoothing to its side, so the two are
compared like with like. Wind-noise flagging (same band) is an M2 item.
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


def observed_envelope(samples: ArrayLike, rate: int, cfg: EnvelopeConfig | None = None) -> Envelope:
    """Low-frequency loudness envelope of a recording."""
    cfg = cfg or EnvelopeConfig()
    x = to_mono(samples)
    if rate != cfg.rate_hz:
        x = resample(x, rate, cfg.rate_hz)
    x = bandpass(x, cfg.rate_hz, cfg.band_hz, cfg.filter_order)
    t, rms = frame_rms(x, cfg.rate_hz, cfg.frame_s)
    values = smooth(rms, cfg.frame_s, cfg.smoothing_sigma_s)
    return Envelope(t=t, values=values, frame_s=cfg.frame_s)


def resample_envelope(env: Envelope, step_s: float) -> Envelope:
    """Linear interpolation of an envelope onto a coarser or finer uniform step."""
    t = np.arange(env.t[0], env.t[-1] + 1e-9, step_s)
    return Envelope(t=t, values=np.interp(t, env.t, env.values), frame_s=step_s)
