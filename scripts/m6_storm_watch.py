"""M6 storm watch: where GLM sees lightning right now, the trigger for off-season thunder cases.

The handoff's protocol records public bird-feeder livestreams when GLM shows lightning nearby,
within each stream's terms, while storm season is out. This script polls the newest GLM L2 LCFA
files on AWS (near real time, about a minute behind the satellite) and reports the flashes of
the last few minutes: for named points, the count within a radius, the nearest flash and the
time of the last one; for a region, the half-degree cells with the most flashes. It never
touches the hour cache, where a partial hour would be kept as if complete: raw files go to
data/cache/glm/_watch and are deleted after each poll.

With --day it looks back instead: flashes per hour of a past UTC day around each point and in
the region, from the hour cache, which is how to find the hour a storm sat over a webcam whose
location is known before pulling that hour of its archive.

Examples:
  uv run python scripts/m6_storm_watch.py --bbox=-105,25,-75,42 --minutes 15
  uv run python scripts/m6_storm_watch.py --point feeder,30.27,-97.74 --radius-km 25 --watch 600
  uv run python scripts/m6_storm_watch.py --day 2025-05-16 --point cam,37.375,-89.745
"""

from __future__ import annotations

import argparse
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from earwitness.core import geodesy
from earwitness.core.grid import BBox, parse_bbox
from earwitness.data.cache import cache_root, download
from earwitness.data.glm import (
    list_hour,
    load_flashes,
    object_url,
    read_glm_file,
    resolve_satellite,
)
from earwitness.project import parse_utc_date, utc_now

KEY_START = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d")
CELL_DEG = 0.5


@dataclass(frozen=True)
class Point:
    """A named place to watch, such as a livestream camera."""

    name: str
    lat: float
    lon: float

    @classmethod
    def parse(cls, text: str) -> Point:
        """'name,lat,lon'."""
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 3:
            raise argparse.ArgumentTypeError(f"point must be name,lat,lon: {text!r}")
        return cls(parts[0], float(parts[1]), float(parts[2]))


@dataclass(frozen=True)
class Flashes:
    """Flash times and positions from one or more files."""

    time_ns: NDArray[np.int64]
    lat: NDArray[np.float64]
    lon: NDArray[np.float64]

    def __len__(self) -> int:
        return int(self.time_ns.size)

    @classmethod
    def empty(cls) -> Flashes:
        return cls(np.zeros(0, np.int64), np.zeros(0, np.float64), np.zeros(0, np.float64))

    @classmethod
    def concat(cls, parts: list[Flashes]) -> Flashes:
        if not parts:
            return cls.empty()
        return cls(
            np.concatenate([p.time_ns for p in parts]),
            np.concatenate([p.lat for p in parts]),
            np.concatenate([p.lon for p in parts]),
        )


def key_start(key: str) -> datetime | None:
    """Start time encoded in a GLM object key (`_sYYYYDDDHHMMSSt`)."""
    m = KEY_START.search(key)
    if m is None:
        return None
    year, doy, hh, mm, ss = (int(x) for x in m.groups())
    return datetime(year, 1, 1, tzinfo=UTC) + timedelta(
        days=doy - 1, hours=hh, minutes=mm, seconds=ss
    )


def recent_keys(satellite: str, start: datetime, end: datetime) -> list[str]:
    """Object keys of the files that start inside [start, end]."""
    keys: set[str] = set()
    cursor = start.replace(minute=0, second=0, microsecond=0)
    while cursor <= end:
        for key, _size in list_hour(satellite, cursor.date(), cursor.hour):
            t = key_start(key)
            if t is not None and start <= t <= end:
                keys.add(key)
        cursor += timedelta(hours=1)
    return sorted(keys)


def fetch(satellite: str, keys: list[str], raw_dir: Path, workers: int = 8) -> dict[str, Flashes]:
    """Download and parse the files, one Flashes per key (quality-0 flashes only); raw files are
    deleted afterward."""
    raw_dir.mkdir(parents=True, exist_ok=True)

    def grab(key: str) -> tuple[str, Flashes]:
        batch = read_glm_file(download(object_url(satellite, key), raw_dir / Path(key).name))
        good = np.asarray(batch.quality) == 0  # the flashes the thunder pipeline keeps
        return key, Flashes(
            np.asarray(batch.time_ns, np.int64)[good],
            np.asarray(batch.lat, np.float64)[good],
            np.asarray(batch.lon, np.float64)[good],
        )

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return dict(pool.map(grab, keys))
    finally:
        shutil.rmtree(raw_dir, ignore_errors=True)


def stamp(ns: int | np.integer) -> str:
    """HH:MM:SSZ of a nanosecond Unix time."""
    return datetime.fromtimestamp(int(ns) / 1e9, tz=UTC).strftime("%H:%M:%SZ")


def point_lines(flashes: Flashes, points: list[Point], radius_m: float) -> list[str]:
    """One report line per watched point."""
    lines = []
    for p in points:
        if len(flashes) == 0:
            lines.append(f"  {p.name:<16} no flashes in the window")
            continue
        d = np.asarray(geodesy.horizontal_distance(flashes.lat, flashes.lon, p.lat, p.lon))
        near = d <= radius_m
        nearest_km = float(d.min()) / 1000.0
        if near.any():
            last = stamp(flashes.time_ns[near].max())
            lines.append(
                f"  {p.name:<16} {int(near.sum()):5d} flashes within {radius_m / 1000:g} km, "
                f"nearest {nearest_km:.1f} km, last at {last}"
            )
        else:
            lines.append(
                f"  {p.name:<16}     0 flashes within {radius_m / 1000:g} km "
                f"(nearest {nearest_km:.0f} km)"
            )
    return lines


def cell_lines(flashes: Flashes, bbox: BBox, limit: int = 10) -> list[str]:
    """The busiest half-degree cells inside the region."""
    inside = (
        (flashes.lon >= bbox.west)
        & (flashes.lon <= bbox.east)
        & (flashes.lat >= bbox.south)
        & (flashes.lat <= bbox.north)
    )
    if not inside.any():
        return ["  no flashes inside the region"]
    lat, lon, t = flashes.lat[inside], flashes.lon[inside], flashes.time_ns[inside]
    ci = np.floor((lon - bbox.west) / CELL_DEG).astype(np.int64)
    cj = np.floor((lat - bbox.south) / CELL_DEG).astype(np.int64)
    cells, inverse, counts = np.unique(ci * 100000 + cj, return_inverse=True, return_counts=True)
    order = np.argsort(-counts)[:limit]
    lines = [f"  {int(inside.sum())} flashes inside the region; busiest {CELL_DEG:g} degree cells:"]
    for k in order:
        i, j = divmod(int(cells[k]), 100000)
        clat = bbox.south + (j + 0.5) * CELL_DEG
        clon = bbox.west + (i + 0.5) * CELL_DEG
        last = stamp(t[inverse == k].max())
        lines.append(
            f"    lat {clat:7.2f}  lon {clon:8.2f}  {int(counts[k]):5d} flashes, last at {last}"
        )
    return lines


def poll(
    satellite: str,
    minutes: float,
    held: dict[str, Flashes],
    raw_dir: Path,
    workers: int,
) -> tuple[Flashes, datetime, datetime, int]:
    """Fetch what is new, drop what fell out of the window, return the window's flashes."""
    end = utc_now()
    start = end - timedelta(minutes=minutes)
    keys = recent_keys(satellite, start, end)
    for stale in [k for k in held if k not in keys]:
        del held[stale]
    missing = [k for k in keys if k not in held]
    if missing:
        held.update(fetch(satellite, missing, raw_dir, workers))
    window = Flashes.concat([held[k] for k in keys if k in held])
    lo, hi = int(start.timestamp() * 1e9), int(end.timestamp() * 1e9)
    keep = (window.time_ns >= lo) & (window.time_ns <= hi)
    return (
        Flashes(window.time_ns[keep], window.lat[keep], window.lon[keep]),
        start,
        end,
        len(missing),
    )


def busiest_window(time_unix: np.ndarray, length_s: float) -> tuple[float | None, int]:
    """Start time and count of the busiest window of `length_s` seconds."""
    if time_unix.size == 0:
        return None, 0
    t = np.sort(time_unix)
    hi = np.searchsorted(t, t + length_s, side="left")
    counts = hi - np.arange(t.size)
    k = int(np.argmax(counts))
    return float(t[k]), int(counts[k])


def day_lines(
    satellite: str,
    day: date,
    points: list[Point],
    bbox: BBox | None,
    radius_m: float,
    root: Path | None,
    workers: int,
    fetch_missing: bool = True,
) -> list[str]:
    """Flashes per hour of one past UTC day around each point and inside the region.

    Reads the hour cache and, unless `fetch_missing` is off, downloads the hours that are not
    cached yet (about 2.6 GB of raw files for a full day, kept as a few MB of Parquet per hour).
    """
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    lines = [f"GLM {satellite}, {day.isoformat()}, flashes per UTC hour 00 to 23:"]
    targets: list[tuple[str, BBox, Point | None]] = [
        (p.name, BBox.around(p.lat, p.lon, radius_m), p) for p in points
    ]
    if bbox is not None:
        targets.append(("region", bbox, None))
    for name, box, point in targets:
        ev = load_flashes(
            satellite,
            start,
            end,
            bbox=box,
            root=root,
            fetch=fetch_missing,
            workers=workers,
            log=print,
        )
        if point is not None and len(ev):
            d = np.asarray(geodesy.horizontal_distance(ev.lat, ev.lon, point.lat, point.lon))
            ev = ev.subset(d <= radius_m)
        hours = ((ev.time_unix - start.timestamp()) // 3600.0).astype(np.int64)
        counts = np.bincount(np.clip(hours, 0, 23), minlength=24)
        t0, n = busiest_window(ev.time_unix, 1800.0)
        best = (
            f"busiest 30 min from {datetime.fromtimestamp(t0, tz=UTC):%H:%M}Z with {n} flashes"
            if t0 is not None
            else "no flashes"
        )
        row = " ".join(f"{int(c):4d}" for c in counts)
        lines.append(f"  {name:<16} {row} | {len(ev)} total; {best}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--bbox", nargs="+", default=None, help="region west south east north")
    ap.add_argument(
        "--day",
        default=None,
        help="a past UTC day, YYYY-MM-DD: flashes per hour from the hour cache instead of the "
        "live feed (downloads the day's missing hours, about 2.6 GB of raw files)",
    )
    ap.add_argument(
        "--cached-only", action="store_true", help="with --day: read cached hours, download none"
    )
    ap.add_argument(
        "--point", type=Point.parse, action="append", default=[], help="name,lat,lon (repeatable)"
    )
    ap.add_argument("--radius-km", type=float, default=25.0, help="around each point (default 25)")
    ap.add_argument("--minutes", type=float, default=15.0, help="window length (default 15)")
    ap.add_argument("--satellite", default="auto", help="auto, east, west or G16..G19")
    ap.add_argument("--watch", type=float, default=None, help="repeat every this many seconds")
    ap.add_argument("--cache", type=Path, default=None, help="cache root (default data/cache)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)

    bbox = parse_bbox(args.bbox) if args.bbox else None
    points: list[Point] = args.point
    if bbox is None and not points:
        raise SystemExit("give --bbox and/or at least one --point")
    span = bbox
    if span is None:
        lats = [p.lat for p in points]
        lons = [p.lon for p in points]
        span = BBox(min(lons) - 0.5, min(lats) - 0.5, max(lons) + 0.5, max(lats) + 0.5)
    if args.day:
        day = parse_utc_date(args.day).date()
        satellite = resolve_satellite(args.satellite, day, span)
        lines = day_lines(
            satellite,
            day,
            points,
            bbox,
            args.radius_km * 1000.0,
            args.cache,
            args.workers,
            fetch_missing=not args.cached_only,
        )
        print("\n".join(lines))
        return 0
    satellite = resolve_satellite(args.satellite, utc_now().date(), span)
    raw_dir = cache_root(args.cache) / "glm" / "_watch"
    held: dict[str, Flashes] = {}
    while True:
        flashes, start, end, fetched = poll(satellite, args.minutes, held, raw_dir, args.workers)
        print(
            f"GLM {satellite}, {start:%H:%M} to {end:%H:%M:%S} UTC: {len(flashes)} flashes in "
            f"{len(held)} files ({fetched} new)"
        )
        if points:
            print("\n".join(point_lines(flashes, points, args.radius_km * 1000.0)))
        if bbox is not None:
            print("\n".join(cell_lines(flashes, bbox)))
        if args.watch is None:
            return 0
        print(f"next poll in {args.watch:g} s", flush=True)
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
