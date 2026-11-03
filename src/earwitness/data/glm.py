"""GOES GLM L2 LCFA lightning flashes from NOAA's AWS Open Data buckets.

Files cover the full disk every 20 seconds (about 180 per hour, 0.6 MB each). The loader lists
an hour's keys through the anonymous S3 listing API, downloads them in parallel, keeps the flash
records (centroid position, first-event time, energy, area, quality flag), writes one Parquet
file per satellite and hour into the cache, and deletes the raw NetCDF files. Bounding-box and
time filters are applied when loading, so one download serves every region.

GLM is optical and space based: it reports flash centroids, includes in-cloud flashes, and its
typical location error is a few kilometers (see docs/HANDOFF.md).
"""

from __future__ import annotations

import shutil
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import xarray as xr
from numpy.typing import NDArray

from earwitness.core.grid import BBox
from earwitness.data.adapters import LightningEvents
from earwitness.data.cache import cache_root, download, fetch_text

Log = Callable[[str], None] | None

BUCKETS = {"G16": "noaa-goes16", "G17": "noaa-goes17", "G18": "noaa-goes18", "G19": "noaa-goes19"}
PRODUCT = "GLM-L2-LCFA"

# Operational periods per orbital slot. GOES-19 replaced GOES-16 as GOES-East in April 2025;
# GOES-18 replaced GOES-17 as GOES-West in January 2023.
EAST = [("G16", date(2017, 12, 18), date(2025, 4, 6)), ("G19", date(2025, 4, 7), None)]
WEST = [("G17", date(2019, 2, 12), date(2023, 1, 3)), ("G18", date(2023, 1, 4), None)]
EAST_WEST_SPLIT_LON = -110.0


def satellite_for(day: date, position: str = "east") -> str:
    """Operational GLM satellite for a date and slot ('east' or 'west')."""
    table = EAST if position == "east" else WEST if position == "west" else None
    if table is None:
        raise ValueError("position must be 'east' or 'west'")
    for sat, start, end in table:
        if day >= start and (end is None or day <= end):
            return sat
    raise ValueError(f"no operational GOES-{position} GLM on {day.isoformat()}")


def position_for(bbox: BBox) -> str:
    """Slot that sees a region best: west of 110 W uses GOES-West, else GOES-East."""
    return "east" if 0.5 * (bbox.west + bbox.east) >= EAST_WEST_SPLIT_LON else "west"


def resolve_satellite(spec: str, day: date, bbox: BBox | None) -> str:
    """Turn 'auto', 'east', 'west' or an explicit G16..G19 into a satellite id."""
    s = spec.strip().upper()
    if s in BUCKETS:
        return s
    if s == "AUTO":
        return satellite_for(day, position_for(bbox) if bbox else "east")
    if s in ("EAST", "WEST"):
        return satellite_for(day, s.lower())
    raise ValueError(f"unknown satellite spec {spec!r}")


def s3_prefix(day: date, hour: int) -> str:
    """Key prefix of one hour of files."""
    return f"{PRODUCT}/{day.year}/{day.timetuple().tm_yday:03d}/{hour:02d}/"


def listing_url(satellite: str, prefix: str, token: str | None = None) -> str:
    """Anonymous ListObjectsV2 URL for a prefix."""
    q = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
    if token:
        q["continuation-token"] = token
    return f"https://{BUCKETS[satellite]}.s3.amazonaws.com/?{urllib.parse.urlencode(q)}"


def object_url(satellite: str, key: str) -> str:
    """Public URL of one object."""
    return f"https://{BUCKETS[satellite]}.s3.amazonaws.com/{key}"


def parse_listing(xml_text: str) -> tuple[list[tuple[str, int]], str | None]:
    """Keys and sizes from a ListObjectsV2 response, plus the continuation token if truncated."""
    root = ET.fromstring(xml_text)
    ns = {"s3": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    prefix = "s3:" if ns else ""
    items = []
    for node in root.findall(f"{prefix}Contents", ns):
        key = node.findtext(f"{prefix}Key", default="", namespaces=ns)
        size = int(node.findtext(f"{prefix}Size", default="0", namespaces=ns))
        if key.endswith(".nc"):
            items.append((key, size))
    truncated = root.findtext(f"{prefix}IsTruncated", default="false", namespaces=ns) == "true"
    token = root.findtext(f"{prefix}NextContinuationToken", default=None, namespaces=ns)
    return items, (token if truncated else None)


def list_hour(satellite: str, day: date, hour: int) -> list[tuple[str, int]]:
    """All file keys for one hour, following pagination."""
    prefix = s3_prefix(day, hour)
    items: list[tuple[str, int]] = []
    token: str | None = None
    while True:
        page, token = parse_listing(fetch_text(listing_url(satellite, prefix, token)))
        items.extend(page)
        if token is None:
            return items


@dataclass(frozen=True)
class FlashBatch:
    """Flash arrays from one or more files."""

    time_ns: NDArray[np.int64]
    lat: NDArray[np.float32]
    lon: NDArray[np.float32]
    energy_j: NDArray[np.float32]
    area_m2: NDArray[np.float32]
    quality: NDArray[np.int8]
    flash_id: NDArray[np.uint16]
    satellite: str


def read_glm_file(path: str | Path) -> FlashBatch:
    """Flash records from one GLM L2 LCFA NetCDF file."""
    with xr.open_dataset(path, engine="h5netcdf") as ds:
        times = ds["flash_time_offset_of_first_event"].values.astype("datetime64[ns]")
        return FlashBatch(
            time_ns=times.astype("int64"),
            lat=ds["flash_lat"].values.astype(np.float32),
            lon=ds["flash_lon"].values.astype(np.float32),
            energy_j=ds["flash_energy"].values.astype(np.float32),
            area_m2=ds["flash_area"].values.astype(np.float32),
            quality=ds["flash_quality_flag"].values.astype(np.int8),
            flash_id=ds["flash_id"].values.astype(np.uint16),
            satellite=str(ds.attrs.get("platform_ID", "")),
        )


def flashes_table(batches: Iterable[FlashBatch], satellite: str) -> pa.Table:
    """Concatenate batches into the Parquet schema the cache uses."""
    parts = list(batches)

    def cat(name: str, dtype: type) -> NDArray:
        if not parts:
            return np.zeros(0, dtype=dtype)
        return np.concatenate([getattr(p, name) for p in parts]).astype(dtype)

    time_ns = cat("time_ns", np.int64)
    order = np.argsort(time_ns, kind="stable")
    n = time_ns.size
    return pa.table(
        {
            "time": pa.array(time_ns[order], type=pa.timestamp("ns", tz="UTC")),
            "lat": pa.array(cat("lat", np.float32)[order]),
            "lon": pa.array(cat("lon", np.float32)[order]),
            "energy_j": pa.array(cat("energy_j", np.float32)[order]),
            "area_m2": pa.array(cat("area_m2", np.float32)[order]),
            "quality": pa.array(cat("quality", np.int8)[order]),
            "flash_id": pa.array(cat("flash_id", np.uint16)[order]),
            "satellite": pa.array([satellite] * n, type=pa.string()),
        }
    )


def hour_cache_path(root: Path, satellite: str, day: date, hour: int) -> Path:
    """Parquet file for one satellite-hour."""
    doy = day.timetuple().tm_yday
    return root / "glm" / satellite / f"{day.year}" / f"{doy:03d}" / f"{hour:02d}.parquet"


def fetch_hour(
    satellite: str,
    day: date,
    hour: int,
    root: Path | None = None,
    workers: int = 8,
    force: bool = False,
    log: Log = None,
) -> Path:
    """Download, convert and cache one hour; returns the Parquet path (fast when cached)."""
    root = cache_root(root)
    dest = hour_cache_path(root, satellite, day, hour)
    if dest.exists() and not force:
        return dest
    keys = list_hour(satellite, day, hour)
    if not keys:
        raise RuntimeError(f"no GLM files listed for {satellite} {day} hour {hour:02d}")
    raw_dir = root / "glm" / "_raw" / f"{satellite}-{day.isoformat()}-{hour:02d}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    if log:
        total_mb = sum(size for _, size in keys) / 1e6
        log(f"GLM {satellite} {day} {hour:02d}Z: {len(keys)} files, {total_mb:.0f} MB")

    def grab(key: str) -> Path:
        return download(object_url(satellite, key), raw_dir / Path(key).name)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        paths = list(pool.map(grab, [k for k, _ in keys]))
    batches = [read_glm_file(p) for p in paths]
    table = flashes_table(batches, satellite)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, dest, compression="zstd")
    shutil.rmtree(raw_dir, ignore_errors=True)
    if log:
        log(f"GLM {satellite} {day} {hour:02d}Z: {table.num_rows} flashes cached")
    return dest


def fetch_range(
    satellite: str,
    start: datetime,
    end: datetime,
    root: Path | None = None,
    workers: int = 8,
    log: Log = None,
) -> list[Path]:
    """Cache every hour touching [start, end)."""
    paths = []
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    end_utc = end.astimezone(UTC)
    while cursor < end_utc:
        paths.append(fetch_hour(satellite, cursor.date(), cursor.hour, root, workers, log=log))
        cursor += timedelta(hours=1)
    return paths


def load_flashes(
    satellite: str,
    start: datetime,
    end: datetime,
    bbox: BBox | None = None,
    root: Path | None = None,
    good_only: bool = True,
    fetch: bool = True,
    workers: int = 8,
    log: Log = None,
) -> LightningEvents:
    """Flashes in [start, end) and inside `bbox`, from the cache (downloading when allowed)."""
    root = cache_root(root)
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    if fetch:
        paths = fetch_range(satellite, start_utc, end_utc, root, workers, log)
    else:
        paths = []
        cursor = start_utc.replace(minute=0, second=0, microsecond=0)
        while cursor < end_utc:
            p = hour_cache_path(root, satellite, cursor.date(), cursor.hour)
            if p.exists():
                paths.append(p)
            cursor += timedelta(hours=1)
    tables = [pq.read_table(p) for p in paths]
    if not tables:
        return LightningEvents(np.zeros(0), np.zeros(0), np.zeros(0), source=f"GLM {satellite}")
    table = pa.concat_tables(tables)
    time_unix = np.asarray(table.column("time").to_numpy()).astype("datetime64[ns]")
    time_unix = time_unix.astype("int64") / 1e9
    lat = table.column("lat").to_numpy().astype(float)
    lon = table.column("lon").to_numpy().astype(float)
    keep = (time_unix >= start_utc.timestamp()) & (time_unix < end_utc.timestamp())
    if good_only:
        keep &= table.column("quality").to_numpy() == 0
    if bbox is not None:
        keep &= bbox.contains(lat, lon)
    return LightningEvents(
        time_unix=time_unix[keep],
        lat=lat[keep],
        lon=lon[keep],
        energy=table.column("energy_j").to_numpy().astype(float)[keep],
        source=f"GLM {satellite}",
    )


@dataclass(frozen=True)
class GlmSource:
    """`LightningSource` adapter over the cached GLM archive."""

    satellite: str = "auto"
    root: Path | None = None
    name: str = "GOES GLM L2 LCFA (NOAA open data)"

    def fetch(self, start: datetime, end: datetime, bbox: BBox | None = None) -> LightningEvents:
        """Events in [start, end), downloading hours that are not cached yet."""
        sat = resolve_satellite(self.satellite, start.astimezone(UTC).date(), bbox)
        return load_flashes(sat, start, end, bbox, self.root)
