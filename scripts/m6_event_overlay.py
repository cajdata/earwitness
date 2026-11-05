"""M6 overlay: an event's spectrogram window with its trace and fitted curves, as a PNG.

Green is the trace, red the free fit, blue the profile's slowest and fastest rows, which show
where the curves the trace cannot tell apart diverge (the wings). A fit that leaves the ridge
inside the traced span is wrong; one that leaves it only outside the span is the degeneracy
the profile covers. Writes with the standard library only, no image dependency.

Usage, from the repo root:
  uv run python scripts/m6_event_overlay.py <audio> <events.json> <event id> <out.png>
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from earwitness.aircraft.fit import fit_straight_flyover, preview_curve
from earwitness.audio.decode import read_audio
from earwitness.audio.spectrogram import spectrogram
from earwitness.core.physics import SPEED_OF_SOUND
from earwitness.server.workspace import spectrogram_view

FloatArray = NDArray[np.float64]


def write_rgb_png(path: Path, img: NDArray[np.uint8]) -> None:
    """Write an (h, w, 3) uint8 image as an uncompressed-filter PNG."""
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[i].tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    """Render the overlay."""
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    audio, events_path, eid, out = (
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        sys.argv[3],
        Path(sys.argv[4]),
    )
    events = json.loads(events_path.read_text(encoding="utf-8"))["events"]
    ev = next((e for e in events if e["id"] == eid), None)
    if ev is None or not ev.get("trace"):
        raise SystemExit(f"no event {eid} with a trace in {events_path}")
    tr = np.asarray(ev["trace"], dtype=float)
    t, f = tr[:, 0], tr[:, 1]
    fit = fit_straight_flyover(t, f)
    rows = np.asarray(fit.valley, dtype=float)
    print(
        f"{eid}: fit f0 {fit.f0_hz:.1f} Hz, v {fit.v_mps:.1f} m/s, d {fit.d_m:.0f} m, "
        f"closest approach "
        f"{fit.t0_s:.1f} s (heard {fit.heard_t0_s():.1f} s), rms {fit.rms_hz:.2f} Hz; valley v "
        f"{rows[:, 0].min():.0f}-{rows[:, 0].max():.0f} m/s"
    )
    samples, rate = read_audio(audio, target_rate=4000)
    spec = spectrogram(samples, rate, nfft=4096, hop=512, fmax=450.0)
    span = max(60.0, 2.0 * (t.max() - t.min()))
    t_lo, t_hi = max(0.0, fit.t0_s - span), fit.t0_s + span
    f_lo, f_hi = max(10.0, 0.6 * f.min()), min(440.0, 1.4 * f.max())
    view = spectrogram_view(spec, t_lo, t_hi, f_lo, f_hi, max_cols=4000, max_rows=1000)
    db = np.asarray(view["db"], dtype=float)
    tv = np.asarray(view["t"], dtype=float)
    fv = np.asarray(view["f"], dtype=float)
    gray = (np.clip(db / 24.0, 0, 1) * 255)[::-1]
    img = np.repeat(gray[:, :, None], 3, axis=2).astype(np.uint8)
    dt, dfreq = float(tv[1] - tv[0]), float(fv[1] - fv[0])

    def put(tt: FloatArray, ff: FloatArray, color: tuple[int, int, int]) -> None:
        for a, b in zip(tt, ff, strict=True):
            col = round((a - tv[0]) / dt)
            row = img.shape[0] - 1 - round((b - fv[0]) / dfreq)
            if 0 <= col < img.shape[1] and 0 <= row < img.shape[0]:
                img[row, col] = color

    tt = np.arange(t_lo, t_hi, dt)
    for row in (rows[np.argmin(rows[:, 0])], rows[np.argmax(rows[:, 0])]):
        v, d, f0, t0 = (float(x) for x in row[:4])
        put(tt, preview_curve(tt, f0, v, d, t0, SPEED_OF_SOUND), (60, 120, 255))
    put(
        tt,
        preview_curve(tt, fit.f0_hz, fit.v_mps, fit.d_m, fit.t0_s, SPEED_OF_SOUND),
        (255, 60, 60),
    )
    put(t, f, (0, 255, 0))
    for j, tj in enumerate(tv):
        if abs(tj % 10.0) < dt / 2 + 1e-9:
            img[:8, j] = 255
    write_rgb_png(out, img)
    print(
        f"image {img.shape[1]}x{img.shape[0]}, {t_lo:.0f}-{t_hi:.0f} s, "
        f"{f_lo:.0f}-{f_hi:.0f} Hz -> {out}"
    )


if __name__ == "__main__":
    main()
