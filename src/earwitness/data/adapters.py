"""Lightning event container and the adapter interface every lightning source implements.

Events are kept as plain arrays with Unix UTC seconds, so the search never touches datetimes.
Files come in two shapes: JSON (`earwitness.strikes/1`, a bare list, or a truth.json with an
`events` list) and Parquet (the GLM cache or any table with time, lat and lon columns).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray

from earwitness.core.grid import BBox
from earwitness.project import STRIKES_SCHEMA, parse_utc
from earwitness.thunder.model import StrikeSet

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class LightningEvents:
    """Lightning events with Unix UTC times (s) and WGS84 positions."""

    time_unix: FloatArray
    lat: FloatArray
    lon: FloatArray
    kind: NDArray[np.str_] | None = None
    """Per-event "CG" or "IC"; None when the source does not say (GLM does not)."""
    energy: FloatArray | None = None
    source: str = ""

    def __len__(self) -> int:
        return int(self.time_unix.shape[0])

    def subset(self, mask: NDArray[np.bool_] | NDArray[np.int64]) -> LightningEvents:
        """Events selected by a boolean mask or index array."""
        return LightningEvents(
            time_unix=self.time_unix[mask],
            lat=self.lat[mask],
            lon=self.lon[mask],
            kind=None if self.kind is None else self.kind[mask],
            energy=None if self.energy is None else self.energy[mask],
            source=self.source,
        )

    def within(self, bbox: BBox) -> LightningEvents:
        """Events inside a bounding box."""
        return self.subset(bbox.contains(self.lat, self.lon))

    def between(self, start_unix: float, end_unix: float) -> LightningEvents:
        """Events with start <= time < end."""
        return self.subset((self.time_unix >= start_unix) & (self.time_unix < end_unix))

    def sorted_by_time(self) -> LightningEvents:
        """Events in time order."""
        return self.subset(np.argsort(self.time_unix, kind="stable"))

    def bbox(self) -> BBox:
        """Bounding box of the events."""
        if len(self) == 0:
            raise ValueError("no events")
        return BBox(
            float(self.lon.min()),
            float(self.lat.min()),
            float(self.lon.max()),
            float(self.lat.max()),
        )

    def to_strike_set(self, t_ref_unix: float) -> StrikeSet:
        """Strikes with times relative to a reference instant (e.g. a recording start)."""
        return StrikeSet(
            time_s=self.time_unix - t_ref_unix, lat=self.lat, lon=self.lon, kind=self.kind
        )


class LightningSource(Protocol):
    """A lightning data provider. GLM implements it; MET Norway and commercial feeds can."""

    name: str

    def fetch(self, start: datetime, end: datetime, bbox: BBox | None = None) -> LightningEvents:
        """Return events in [start, end), optionally limited to a bounding box."""
        ...


def _events_from_rows(
    rows: list[dict[str, object]], source: str, t_ref_unix: float | None = None
) -> LightningEvents:
    times = []
    for row in rows:
        if "time_unix" in row:
            times.append(float(row["time_unix"]))  # type: ignore[arg-type]
        elif "time_utc" in row:
            times.append(parse_utc(str(row["time_utc"])).timestamp())
        elif "time_s" in row and t_ref_unix is not None:
            times.append(t_ref_unix + float(row["time_s"]))  # type: ignore[arg-type]
        else:
            raise ValueError("each strike needs time_utc, time_unix, or time_s with a reference")
    kinds = [str(row.get("kind", "CG")) for row in rows]
    energies = [row.get("energy_j") for row in rows]
    has_energy = bool(rows) and all(e is not None for e in energies)
    return LightningEvents(
        time_unix=np.array(times, dtype=float),
        lat=np.array([float(r["lat"]) for r in rows], dtype=float),  # type: ignore[arg-type]
        lon=np.array([float(r["lon"]) for r in rows], dtype=float),  # type: ignore[arg-type]
        kind=np.array(kinds, dtype=str),
        energy=np.array(energies, dtype=float) if has_energy else None,
        source=source,
    )


def _events_from_table(table: pa.Table, source: str) -> LightningEvents:
    cols = set(table.column_names)
    if "time" in cols:
        t = table.column("time").to_numpy()
        time_unix = np.asarray(t).astype("datetime64[ns]").astype("int64") / 1e9
    elif "time_unix" in cols:
        time_unix = table.column("time_unix").to_numpy().astype(float)
    elif "time_utc" in cols:
        time_unix = np.array(
            [parse_utc(str(v)).timestamp() for v in table.column("time_utc").to_pylist()]
        )
    else:
        raise ValueError("table needs a time, time_unix or time_utc column")
    kind = np.array(table.column("kind").to_pylist(), dtype=str) if "kind" in cols else None
    energy = table.column("energy_j").to_numpy().astype(float) if "energy_j" in cols else None
    return LightningEvents(
        time_unix=np.asarray(time_unix, dtype=float),
        lat=table.column("lat").to_numpy().astype(float),
        lon=table.column("lon").to_numpy().astype(float),
        kind=kind,
        energy=energy,
        source=source,
    )


def load_events(path: str | Path, t_ref_unix: float | None = None) -> LightningEvents:
    """Load lightning events from JSON or Parquet.

    `t_ref_unix` resolves JSON rows that carry only a relative `time_s`.
    """
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        return _events_from_table(pq.read_table(p), source=p.name)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        rows = raw
    elif "strikes" in raw:
        rows = raw["strikes"]
    elif "events" in raw:
        rows = [r for r in raw["events"] if "lat" in r and "lon" in r]
    else:
        raise ValueError(f"{p.name}: unrecognized lightning JSON layout")
    return _events_from_rows(rows, source=p.name, t_ref_unix=t_ref_unix)


def events_table(events: LightningEvents) -> pa.Table:
    """Arrow table with a UTC timestamp column, the shape the Parquet cache uses."""
    times = (events.time_unix * 1e9).astype("int64")
    columns: dict[str, pa.Array] = {
        "time": pa.array(times, type=pa.timestamp("ns", tz="UTC")),
        "lat": pa.array(events.lat, type=pa.float64()),
        "lon": pa.array(events.lon, type=pa.float64()),
    }
    if events.kind is not None:
        columns["kind"] = pa.array([str(k) for k in events.kind], type=pa.string())
    if events.energy is not None:
        columns["energy_j"] = pa.array(events.energy, type=pa.float64())
    return pa.table(columns)


def save_events(path: str | Path, events: LightningEvents) -> Path:
    """Write events as Parquet (.parquet) or JSON (anything else)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".parquet":
        pq.write_table(events_table(events), p)
        return p
    rows = []
    for i in range(len(events)):
        when = datetime.fromtimestamp(float(events.time_unix[i]), tz=UTC)
        row: dict[str, object] = {
            "time_utc": when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "lat": float(events.lat[i]),
            "lon": float(events.lon[i]),
            "kind": "CG" if events.kind is None else str(events.kind[i]),
        }
        if events.energy is not None:
            row["energy_j"] = float(events.energy[i])
        rows.append(row)
    payload = {"schema": STRIKES_SCHEMA, "time_format": "iso8601_utc", "strikes": rows}
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return p
