"""M6 spectrogram: whitened overview strips of a recording and 180 s zooms, as PNG files.

The first look at a real recording: strips of --strip seconds between --fmin and --fmax, tick
marks every 10 s (short) and 60 s (tall) along the top and every 50 Hz along the left, with
optional zooms centred on given seconds. Doppler curves show as S-shaped lines, steady tones as
horizontal ones, and the audit report's sweep times tell where to zoom. Standard library only
for the image writing.

Usage, from the repo root:
  uv run python scripts/m6_spectrogram.py <audio> <out_dir> [--strip 300] [--fmin 40]
      [--fmax 300] [--zoom 674 812 ...] [--hi-db 24]
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from earwitness.audio.decode import read_audio
from earwitness.audio.spectrogram import Spectrogram, spectrogram
from earwitness.server.workspace import spectrogram_view


def write_png(path: Path, img: NDArray[np.uint8]) -> None:
    """Write an (h, w) uint8 image as a grayscale PNG."""
    h, w = img.shape
    raw = b"".join(b"\x00" + img[i].tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def strip(
    spec: Spectrogram,
    t_lo: float,
    t_hi: float,
    f_lo: float,
    f_hi: float,
    cols: int,
    rows: int,
    hi_db: float = 24.0,
) -> NDArray[np.uint8]:
    """One strip, low frequency at the bottom, with time and frequency tick marks."""
    view = spectrogram_view(spec, t_lo, t_hi, f_lo, f_hi, max_cols=cols, max_rows=rows)
    db = np.asarray(view["db"], dtype=float)
    if db.size == 0:
        return np.zeros((rows, cols), np.uint8)
    img = (np.clip(db / hi_db, 0, 1) * 255)[::-1].astype(np.uint8)
    t = np.asarray(view["t"], dtype=float)
    dt = float(t[1] - t[0]) if t.size > 1 else 1.0
    for j, tj in enumerate(t):
        if abs(tj % 60.0) < dt / 2 + 1e-9:
            img[:12, j] = 255
        elif abs(tj % 10.0) < dt / 2 + 1e-9:
            img[:5, j] = 255
    f = np.asarray(view["f"], dtype=float)[::-1]
    df = abs(float(f[1] - f[0])) if f.size > 1 else 1.0
    for i, fi in enumerate(f):
        if abs(fi % 50.0) < df / 2 + 1e-9:
            img[i, :12] = 255
    return img


def main() -> None:
    """Render the overview and the zooms."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("audio", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--strip", type=float, default=300.0, help="seconds per strip")
    ap.add_argument("--fmin", type=float, default=40.0)
    ap.add_argument("--fmax", type=float, default=300.0)
    ap.add_argument(
        "--zoom", type=float, nargs="*", default=[], help="centre seconds of 180 s zooms"
    )
    ap.add_argument("--hi-db", type=float, default=24.0, help="whitened dB shown as white")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    samples, rate = read_audio(args.audio, target_rate=4000)
    duration = samples.size / rate
    spec = spectrogram(samples, rate, nfft=4096, hop=512, fmax=args.fmax + 50.0)
    print(
        f"{duration:.1f} s; spectrogram {spec.whitened_db.shape}, "
        f"dt {spec.t[1] - spec.t[0]:.3f} s, "
        f"df {spec.f[1] - spec.f[0]:.3f} Hz"
    )
    n = int(np.ceil(duration / args.strip))
    cols = 1800
    strips: list[NDArray[np.uint8]] = []
    for k in range(n):
        t_lo, t_hi = args.strip * k, min(args.strip * (k + 1), duration)
        s = strip(spec, t_lo, t_hi, args.fmin, args.fmax, cols, 220, args.hi_db)
        pad = np.zeros((s.shape[0], cols), np.uint8)
        pad[:, : s.shape[1]] = s
        strips.append(pad)
        strips.append(np.full((6, cols), 128, np.uint8))
    write_png(args.out_dir / "overview.png", np.vstack(strips[:-1]))
    print(
        f"overview: {n} strips of {args.strip:.0f} s, {args.fmin:.0f}-{args.fmax:.0f} Hz "
        f"-> {args.out_dir / 'overview.png'}"
    )
    for c in args.zoom:
        img = strip(
            spec,
            max(0.0, c - 90.0),
            min(duration, c + 90.0),
            args.fmin,
            args.fmax,
            1400,
            260,
            args.hi_db,
        )
        write_png(args.out_dir / f"zoom_{int(c):04d}.png", img)
        print(f"zoom at {c:.0f} s: {img.shape[1]}x{img.shape[0]}")


if __name__ == "__main__":
    main()
