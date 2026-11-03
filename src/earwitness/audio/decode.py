"""Audio I/O: WAV and other libsndfile formats via soundfile, everything else via ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from numpy.typing import ArrayLike, NDArray
from scipy.signal import resample_poly

FloatArray = NDArray[np.float64]

FFMPEG_FALLBACK_RATE = 48000


def ffmpeg_path() -> str | None:
    """Path of the ffmpeg executable, or None when it is not on PATH."""
    return shutil.which("ffmpeg")


def to_mono(samples: ArrayLike) -> FloatArray:
    """Average channels of a (frames, channels) array; pass 1D arrays through."""
    x = np.asarray(samples, dtype=float)
    return x if x.ndim == 1 else x.mean(axis=1)


def resample(samples: ArrayLike, rate: int, target_rate: int) -> FloatArray:
    """Polyphase resampling by the rational factor target_rate / rate."""
    x = np.asarray(samples, dtype=float)
    if rate == target_rate:
        return x.copy()
    frac = Fraction(int(target_rate), int(rate)).limit_denominator(10000)
    return np.asarray(resample_poly(x, frac.numerator, frac.denominator), dtype=float)


def _read_with_ffmpeg(path: Path, rate: int) -> tuple[FloatArray, int]:
    exe = ffmpeg_path()
    if exe is None:
        raise RuntimeError(f"cannot decode {path.name}: ffmpeg is not on PATH")
    cmd = [
        exe,
        "-v",
        "error",
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        str(rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed on {path.name}: {proc.stderr.decode(errors='replace')}")
    return np.frombuffer(proc.stdout, dtype=np.float32).astype(float), rate


def read_audio(path: str | Path, target_rate: int | None = None) -> tuple[FloatArray, int]:
    """Decode a local file to mono float64 samples and return (samples, rate).

    soundfile handles WAV, FLAC, OGG and friends. Anything else (video containers, MP3, AAC)
    goes through ffmpeg when it is installed. `target_rate` resamples the result.
    """
    p = Path(path)
    try:
        data, rate = sf.read(str(p), dtype="float64", always_2d=True)
        samples = to_mono(data)
    except (sf.LibsndfileError, RuntimeError):
        samples, rate = _read_with_ffmpeg(p, target_rate or FFMPEG_FALLBACK_RATE)
    if target_rate is not None and rate != target_rate:
        samples = resample(samples, rate, target_rate)
        rate = target_rate
    return samples, int(rate)


def write_wav(path: str | Path, samples: ArrayLike, rate: int, subtype: str = "PCM_16") -> Path:
    """Write mono samples in [-1, 1] as WAV; values outside the range are clipped."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    x = np.clip(np.asarray(samples, dtype=float), -1.0, 1.0)
    sf.write(str(p), x, int(rate), subtype=subtype)
    return p
