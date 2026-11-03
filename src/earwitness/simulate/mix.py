"""Mixer: background noise at a set SNR, device-like filtering, lossy codec round trips.

These make synthetic recordings resemble real files so the pipelines are tested against the
degradations the videos showed: added noise, high-pass filtering, AAC and MP3 compression.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.signal import butter, lfilter, sosfilt

from earwitness.audio.decode import ffmpeg_path, write_wav

FloatArray = NDArray[np.float64]

# Paul Kellet's IIR approximation of 1/f noise (within about 0.05 dB above ~1/5000 of the rate).
PINK_B = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
PINK_A = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])

CHUNK = 1 << 20


def rms(x: ArrayLike) -> float:
    """Root mean square, computed in chunks to avoid large temporaries."""
    arr = np.asarray(x)
    if arr.size == 0:
        return 0.0
    acc = 0.0
    for start in range(0, arr.size, CHUNK):
        chunk = arr[start : start + CHUNK].astype(float, copy=False)
        acc += float(np.dot(chunk, chunk))
    return float(np.sqrt(acc / arr.size))


def white_noise(n: int, rng: np.random.Generator) -> FloatArray:
    """Unit-RMS Gaussian white noise."""
    return rng.standard_normal(n)


def pink_noise(n: int, rng: np.random.Generator) -> FloatArray:
    """Unit-RMS pink (1/f) noise, generated in chunks with filter state carried across."""
    out = np.empty(n)
    zi = np.zeros(max(len(PINK_A), len(PINK_B)) - 1)
    for start in range(0, n, CHUNK):
        stop = min(start + CHUNK, n)
        y, zi = lfilter(PINK_B, PINK_A, rng.standard_normal(stop - start), zi=zi)
        out[start:stop] = y
    scale = rms(out)
    return out / scale if scale > 0 else out


def add_noise(
    signal: ArrayLike, snr_db: float, rng: np.random.Generator, color: str = "pink"
) -> FloatArray:
    """Add noise so that 20 log10(rms(signal) / rms(noise)) equals `snr_db` (broadband)."""
    x = np.asarray(signal, dtype=float)
    sig_rms = rms(x)
    if sig_rms == 0.0:
        raise ValueError("cannot set an SNR for an all-zero signal")
    if color == "pink":
        noise = pink_noise(x.size, rng)
    elif color == "white":
        noise = white_noise(x.size, rng)
    else:
        raise ValueError(f"unknown noise color {color!r}")
    return x + noise * (sig_rms / 10.0 ** (snr_db / 20.0))


def highpass(signal: ArrayLike, rate: int, cutoff_hz: float, order: int = 2) -> FloatArray:
    """Causal Butterworth high-pass, like a recorder's low-cut filter."""
    sos = butter(order, cutoff_hz, btype="highpass", fs=rate, output="sos")
    return np.asarray(sosfilt(sos, np.asarray(signal, dtype=float)), dtype=float)


def soft_clip(signal: ArrayLike, level: float) -> FloatArray:
    """Smooth saturation: level * tanh(x / level). Mimics a clipped microphone on close thunder."""
    x = np.asarray(signal, dtype=float)
    return level * np.tanh(x / level)


def normalize_peak(signal: ArrayLike, peak: float = 0.5) -> FloatArray:
    """Scale so the largest absolute sample equals `peak`; all-zero input is returned unchanged."""
    x = np.asarray(signal, dtype=float)
    m = float(np.max(np.abs(x))) if x.size else 0.0
    return x * (peak / m) if m > 0 else x.copy()


def codec_roundtrip(
    signal: ArrayLike, rate: int, codec: str = "aac", bitrate_kbps: int = 96
) -> FloatArray:
    """Encode to AAC (.m4a) or MP3 and decode back with ffmpeg; length is restored by pad/trim.

    Raises RuntimeError when ffmpeg is not on PATH.
    """
    exe = ffmpeg_path()
    if exe is None:
        raise RuntimeError("codec round trip needs ffmpeg on PATH")
    if codec == "aac":
        encoder, ext = "aac", ".m4a"
    elif codec == "mp3":
        encoder, ext = "libmp3lame", ".mp3"
    else:
        raise ValueError(f"unsupported codec {codec!r}; use 'aac' or 'mp3'")
    x = normalize_peak(np.asarray(signal, dtype=float), 0.9)
    with tempfile.TemporaryDirectory() as tmp:
        src = write_wav(Path(tmp) / "in.wav", x, rate, subtype="FLOAT")
        enc = Path(tmp) / f"out{ext}"
        encode = [exe, "-v", "error", "-y", "-i", str(src), "-c:a", encoder]
        encode += ["-b:a", f"{bitrate_kbps}k", str(enc)]
        proc = subprocess.run(encode, capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg encode failed: {proc.stderr.decode(errors='replace')}")
        decode = [exe, "-v", "error", "-i", str(enc), "-ac", "1", "-ar", str(rate)]
        decode += ["-f", "f32le", "pipe:1"]
        proc = subprocess.run(decode, capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg decode failed: {proc.stderr.decode(errors='replace')}")
    y = np.frombuffer(proc.stdout, dtype=np.float32).astype(float)
    if y.size >= x.size:
        return y[: x.size]
    return np.pad(y, (0, x.size - y.size))
