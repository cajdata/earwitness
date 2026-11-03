"""adsb.lol globe_history: one day of readsb traces, streamed from split tar parts into Parquet.

adsb.lol publishes each day as a GitHub release of `adsblol/globe_history_<year>`, tagged
`v<YYYY>.<MM>.<DD>-planes-readsb-prod-0`, whose assets are a tar archive split into 2 GB parts
(`.tar.aa`, `.tar.ab`, ...). Inside, `traces/<xx>/trace_full_<icao>.json` holds one gzip-compressed
JSON trace per aircraft per day in readsb's trace format. The loader streams the concatenated
parts through `tarfile` without reassembling them on disk, keeps the points inside the region and
time window, and writes one Parquet file per day and region. License: ODbL 1.0.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import tarfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray

from earwitness.core.grid import BBox
from earwitness.data.cache import cache_root, download, fetch_text

Log = Callable[[str], None] | None

VARIANTS = ("prod", "staging")
FT_TO_M = 0.3048
KT_TO_MPS = 0.514444

FLAG_STALE = 1
FLAG_NEW_LEG = 2
FLAG_VRATE_GEOMETRIC = 4
FLAG_ALT_GEOMETRIC = 8

POINT_SCHEMA = pa.schema(
    [
        ("icao", pa.string()),
        ("time", pa.timestamp("ns", tz="UTC")),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("alt_baro_ft", pa.float32()),
        ("alt_geom_ft", pa.float32()),
        ("on_ground", pa.bool_()),
        ("gs_kt", pa.float32()),
        ("track_deg", pa.float32()),
        ("vrate_fpm", pa.float32()),
        ("flags", pa.int16()),
        ("source", pa.string()),
        ("type_code", pa.string()),
        ("registration", pa.string()),
        ("description", pa.string()),
        ("db_flags", pa.int32()),
    ]
)


def repo_for(day: date) -> str:
    """GitHub repository holding that year's releases."""
    return f"adsblol/globe_history_{day.year}"


def release_tag(day: date, variant: str = "prod") -> str:
    """Release tag of one day."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    return f"v{day.year}.{day.month:02d}.{day.day:02d}-planes-readsb-{variant}-0"


def asset_url(day: date, name: str, variant: str = "prod") -> str:
    """Public download URL of one release asset."""
    return (
        f"https://github.com/{repo_for(day)}/releases/download/{release_tag(day, variant)}/{name}"
    )


@dataclass(frozen=True)
class Asset:
    """One split tar part."""

    name: str
    size: int
    url: str


def list_assets(day: date, variant: str = "prod") -> list[Asset]:
    """Tar parts of a day's release, in order, from the GitHub API (anonymous, 60 calls/hour)."""
    url = f"https://api.github.com/repos/{repo_for(day)}/releases/tags/{release_tag(day, variant)}"
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    data = json.loads(fetch_text(url, headers=headers))
    assets = [
        Asset(
            a["name"],
            int(a["size"]),
            a.get("browser_download_url") or asset_url(day, a["name"], variant),
        )
        for a in data.get("assets", [])
        if ".tar." in a["name"]
    ]
    assets.sort(key=lambda a: a.name)
    if not assets:
        raise RuntimeError(f"no tar parts in release {release_tag(day, variant)}")
    return assets


def raw_dir(root: Path, day: date) -> Path:
    """Where the split parts of a day are kept while processing."""
    return root / "adsb" / "_raw" / day.isoformat()


def download_parts(
    day: date, root: Path | None = None, variant: str = "prod", log: Log = None
) -> list[Path]:
    """Fetch every part of a day's release unless a complete copy is already present."""
    root = cache_root(root)
    paths = []
    for asset in list_assets(day, variant):
        dest = raw_dir(root, day) / asset.name
        if dest.exists() and dest.stat().st_size == asset.size:
            if log:
                log(f"adsb.lol {asset.name}: already downloaded ({asset.size / 1e9:.2f} GB)")
        else:
            if log:
                log(f"adsb.lol {asset.name}: downloading {asset.size / 1e9:.2f} GB")
            download(asset.url, dest, timeout=600.0)
            if dest.stat().st_size != asset.size:
                raise RuntimeError(
                    f"{asset.name}: got {dest.stat().st_size} bytes, expected {asset.size}"
                )
        paths.append(dest)
    return paths


class ChainedReader(io.RawIOBase):
    """Read several files as one sequential stream, so split tar parts need no reassembly."""

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self._paths = list(paths)
        self._index = 0
        self._file: io.BufferedReader | None = None
        self._open_current()

    def _open_current(self) -> None:
        if self._file is not None:
            self._file.close()
        if self._index < len(self._paths):
            self._file = self._paths[self._index].open("rb")
        else:
            self._file = None

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        view = memoryview(buffer)
        filled = 0
        while filled < len(view) and self._file is not None:
            n = self._file.readinto(view[filled:])
            if n:
                filled += n
                continue
            self._index += 1
            self._open_current()
        return filled

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        super().close()


def iter_trace_files(parts: list[Path]) -> Iterator[tuple[str, bytes]]:
    """Yield (member name, raw bytes) for every trace file in the streamed archive."""
    with ChainedReader(parts) as stream, tarfile.open(fileobj=stream, mode="r|") as tar:
        for member in tar:
            if not member.isfile():
                continue
            name = member.name
            base = name.rsplit("/", 1)[-1]
            if "traces/" not in name or not base.startswith("trace_full_"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            yield name, handle.read()


@dataclass
class Trace:
    """One aircraft's day of positions, as parallel arrays."""

    icao: str
    registration: str
    type_code: str
    description: str
    db_flags: int
    time_unix: NDArray[np.float64]
    lat: NDArray[np.float64]
    lon: NDArray[np.float64]
    alt_baro_ft: NDArray[np.float32]
    alt_geom_ft: NDArray[np.float32]
    on_ground: NDArray[np.bool_]
    gs_kt: NDArray[np.float32]
    track_deg: NDArray[np.float32]
    vrate_fpm: NDArray[np.float32]
    flags: NDArray[np.int16]
    source: list[str]

    def __len__(self) -> int:
        return int(self.time_unix.shape[0])

    def subset(self, mask: NDArray[np.bool_]) -> Trace:
        """Points selected by a mask."""
        idx = np.flatnonzero(mask)
        return Trace(
            self.icao,
            self.registration,
            self.type_code,
            self.description,
            self.db_flags,
            self.time_unix[idx],
            self.lat[idx],
            self.lon[idx],
            self.alt_baro_ft[idx],
            self.alt_geom_ft[idx],
            self.on_ground[idx],
            self.gs_kt[idx],
            self.track_deg[idx],
            self.vrate_fpm[idx],
            self.flags[idx],
            [self.source[i] for i in idx],
        )


def _num(value: object) -> float:
    return float(value) if isinstance(value, int | float) else np.nan


def parse_trace(raw: bytes) -> Trace | None:
    """Decode one trace file (gzip or plain JSON). Returns None when it has no positions."""
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    doc = json.loads(data)
    points = doc.get("trace") or []
    if not points:
        return None
    base = float(doc.get("timestamp", 0.0))
    n = len(points)
    time_unix = np.empty(n)
    lat = np.empty(n)
    lon = np.empty(n)
    alt_baro = np.full(n, np.nan, dtype=np.float32)
    alt_geom = np.full(n, np.nan, dtype=np.float32)
    on_ground = np.zeros(n, dtype=bool)
    gs = np.full(n, np.nan, dtype=np.float32)
    track = np.full(n, np.nan, dtype=np.float32)
    vrate = np.full(n, np.nan, dtype=np.float32)
    flags = np.zeros(n, dtype=np.int16)
    source: list[str] = []
    for i, p in enumerate(points):
        time_unix[i] = base + float(p[0])
        lat[i] = float(p[1])
        lon[i] = float(p[2])
        alt = p[3]
        if alt == "ground":
            on_ground[i] = True
        else:
            alt_baro[i] = _num(alt)
        gs[i] = _num(p[4])
        track[i] = _num(p[5])
        flags[i] = int(p[6]) if isinstance(p[6], int) else 0
        vrate[i] = _num(p[7])
        source.append(str(p[9]) if len(p) > 9 and p[9] is not None else "")
        if len(p) > 10:
            alt_geom[i] = _num(p[10])
    return Trace(
        icao=str(doc.get("icao", "")),
        registration=str(doc.get("r", "") or ""),
        type_code=str(doc.get("t", "") or ""),
        description=str(doc.get("desc", "") or ""),
        db_flags=int(doc.get("dbFlags", 0) or 0),
        time_unix=time_unix,
        lat=lat,
        lon=lon,
        alt_baro_ft=alt_baro,
        alt_geom_ft=alt_geom,
        on_ground=on_ground,
        gs_kt=gs,
        track_deg=track,
        vrate_fpm=vrate,
        flags=flags,
        source=source,
    )


def clip_trace(
    trace: Trace, bbox: BBox | None, t_start: float | None, t_end: float | None, min_points: int = 2
) -> Trace | None:
    """Keep the points inside the region and time window; None when too few remain."""
    keep = np.ones(len(trace), dtype=bool)
    if bbox is not None:
        keep &= bbox.contains(trace.lat, trace.lon)
    if t_start is not None:
        keep &= trace.time_unix >= t_start
    if t_end is not None:
        keep &= trace.time_unix < t_end
    if np.count_nonzero(keep) < min_points:
        return None
    return trace.subset(keep)


def _batch(traces: list[Trace]) -> pa.RecordBatch:
    def cat(name: str) -> np.ndarray:
        return np.concatenate([getattr(t, name) for t in traces])

    n_each = [len(t) for t in traces]

    def rep(values: list[object]) -> list[object]:
        out: list[object] = []
        for v, n in zip(values, n_each, strict=True):
            out.extend([v] * n)
        return out

    time_ns = (cat("time_unix") * 1e9).astype("int64")
    return pa.record_batch(
        [
            pa.array(rep([t.icao for t in traces]), type=pa.string()),
            pa.array(time_ns, type=pa.timestamp("ns", tz="UTC")),
            pa.array(cat("lat")),
            pa.array(cat("lon")),
            pa.array(cat("alt_baro_ft"), type=pa.float32()),
            pa.array(cat("alt_geom_ft"), type=pa.float32()),
            pa.array(cat("on_ground")),
            pa.array(cat("gs_kt"), type=pa.float32()),
            pa.array(cat("track_deg"), type=pa.float32()),
            pa.array(cat("vrate_fpm"), type=pa.float32()),
            pa.array(cat("flags"), type=pa.int16()),
            pa.array([s for t in traces for s in t.source], type=pa.string()),
            pa.array(rep([t.type_code for t in traces]), type=pa.string()),
            pa.array(rep([t.registration for t in traces]), type=pa.string()),
            pa.array(rep([t.description for t in traces]), type=pa.string()),
            pa.array(rep([t.db_flags for t in traces]), type=pa.int32()),
        ],
        schema=POINT_SCHEMA,
    )


def region_key(bbox: BBox) -> str:
    """File-name-safe region label."""
    return f"{bbox.west:.2f}_{bbox.south:.2f}_{bbox.east:.2f}_{bbox.north:.2f}".replace("-", "m")


def day_cache_path(root: Path, day: date, bbox: BBox, variant: str = "prod") -> Path:
    """Parquet file for one day and region."""
    return root / "adsb" / variant / day.isoformat() / f"{region_key(bbox)}.parquet"


@dataclass(frozen=True)
class BuildStats:
    """What a day build saw and kept."""

    traces_read: int
    aircraft_kept: int
    points_kept: int
    path: Path


def build_day(
    day: date,
    bbox: BBox,
    root: Path | None = None,
    variant: str = "prod",
    parts: list[Path] | None = None,
    keep_raw: bool = False,
    log: Log = None,
    flush_points: int = 2_000_000,
) -> BuildStats:
    """Download (unless `parts` is given), stream, clip to the region, write the Parquet."""
    root = cache_root(root)
    dest = day_cache_path(root, day, bbox, variant)
    if parts is None:
        parts = download_parts(day, root, variant, log)
    t_start = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
    t_end = t_start + 86400.0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".parquet.tmp")
    writer = pq.ParquetWriter(tmp, POINT_SCHEMA, compression="zstd")
    pending: list[Trace] = []
    pending_points = 0
    traces_read = aircraft_kept = points_kept = 0
    try:
        for _, raw in iter_trace_files(parts):
            traces_read += 1
            if log and traces_read % 20000 == 0:
                log(f"adsb.lol {day}: {traces_read} traces read, {aircraft_kept} aircraft kept")
            trace = parse_trace(raw)
            if trace is None:
                continue
            clipped = clip_trace(trace, bbox, t_start, t_end)
            if clipped is None:
                continue
            pending.append(clipped)
            pending_points += len(clipped)
            aircraft_kept += 1
            points_kept += len(clipped)
            if pending_points >= flush_points:
                writer.write_batch(_batch(pending))
                pending, pending_points = [], 0
        if pending:
            writer.write_batch(_batch(pending))
    finally:
        writer.close()
    tmp.replace(dest)
    if not keep_raw and parts and all(p.parent == raw_dir(root, day) for p in parts):
        for p in parts:
            p.unlink(missing_ok=True)
    if log:
        log(f"adsb.lol {day}: {aircraft_kept} aircraft, {points_kept} points in {bbox} -> {dest}")
    return BuildStats(traces_read, aircraft_kept, points_kept, dest)


def load_day(
    day: date,
    bbox: BBox,
    root: Path | None = None,
    variant: str = "prod",
    t_start: datetime | None = None,
    t_end: datetime | None = None,
    sub_bbox: BBox | None = None,
) -> pa.Table:
    """Read a cached day, optionally narrowed in time or to a smaller box."""
    root = cache_root(root)
    path = day_cache_path(root, day, bbox, variant)
    if not path.exists():
        raise FileNotFoundError(
            f"no cached adsb.lol day at {path}; run `earwitness data adsb` first"
        )
    table = pq.read_table(path)
    keep = np.ones(table.num_rows, dtype=bool)
    if t_start is not None or t_end is not None:
        t = (
            np.asarray(table.column("time").to_numpy()).astype("datetime64[ns]").astype("int64")
            / 1e9
        )
        if t_start is not None:
            keep &= t >= t_start.timestamp()
        if t_end is not None:
            keep &= t < t_end.timestamp()
    if sub_bbox is not None:
        keep &= sub_bbox.contains(table.column("lat").to_numpy(), table.column("lon").to_numpy())
    return table.filter(pa.array(keep)) if not keep.all() else table


def extract_traces(parts: list[Path], icaos: set[str]) -> dict[str, Trace]:
    """Parse only the named aircraft from a streamed release; used to compare instances."""
    wanted = {i.lower() for i in icaos}
    found: dict[str, Trace] = {}
    for name, raw in iter_trace_files(parts):
        base = name.rsplit("/", 1)[-1]
        icao = base[len("trace_full_") : -len(".json")].lower()
        if icao not in wanted:
            continue
        trace = parse_trace(raw)
        if trace is not None:
            found[icao] = trace
        if len(found) == len(wanted):
            break
    return found


def cached_regions(root: Path | None, day: date, variant: str = "prod") -> list[tuple[BBox, Path]]:
    """Regions already cached for a day, largest first."""
    folder = cache_root(root) / "adsb" / variant / day.isoformat()
    out: list[tuple[BBox, Path]] = []
    for path in folder.glob("*.parquet") if folder.exists() else []:
        try:
            w, s, e, n = (float(v.replace("m", "-")) for v in path.stem.split("_"))
            out.append((BBox(w, s, e, n), path))
        except ValueError:
            continue
    out.sort(key=lambda item: -item[0].area_km2())
    return out


def history_trace_url(day: date, icao: str) -> str:
    """adsb.lol's per-day trace file for one aircraft; the replay view loads the same file."""
    icao = icao.lower()
    return (
        f"https://adsb.lol/globe_history/{day.year}/{day.month:02d}/{day.day:02d}/"
        f"traces/{icao[-2:]}/trace_full_{icao}.json"
    )
