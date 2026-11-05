"""Flight preparation: cached ADS-B points to flights with one vertical datum and 1 s tracks.

Each aircraft's day is split into legs at readsb's new-leg flag, at gaps longer than `gap_s`,
and at ground-to-air transitions. Heights prefer geometric (GNSS, WGS84 ellipsoid) altitude;
where only barometric altitude exists it is shifted by the leg's median geometric-minus-baro
offset, or kept as is and flagged. Tracks are interpolated to a uniform step in local ENU with a
shape-preserving (PCHIP) interpolant, so irregular 1 to 10 s ADS-B samples do not overshoot.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from numpy.typing import NDArray
from scipy.interpolate import PchipInterpolator

from earwitness.aircraft.model import Track, uniform_times
from earwitness.core import geodesy
from earwitness.data.adsb_lol import FLAG_NEW_LEG, FT_TO_M, KT_TO_MPS
from earwitness.data.aircraft_types import TypeTable, classify

FloatArray = NDArray[np.float64]


@dataclass
class Flight:
    """One airborne leg of one aircraft with ellipsoidal heights."""

    icao: str
    leg: int
    type_code: str
    description: str
    engine_class: str
    """'piston', 'turboprop', 'jet', 'helicopter', 'other' or 'unknown'."""
    time_unix: FloatArray
    lat: FloatArray
    lon: FloatArray
    height_m: FloatArray
    """Height above the WGS84 ellipsoid, m."""
    gs_mps: FloatArray
    height_source: str
    """'geometric', 'baro_shifted' or 'baro_uncorrected'."""
    registration: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.time_unix.shape[0])

    @property
    def span(self) -> tuple[float, float]:
        """First and last time."""
        return float(self.time_unix[0]), float(self.time_unix[-1])

    def is_propeller(self) -> bool:
        """Piston or turboprop."""
        return self.engine_class in ("piston", "turboprop")

    def track(
        self, ref_lat: float, ref_lon: float, ref_h: float = 0.0, step_s: float = 1.0
    ) -> Track:
        """Uniform-step ENU track about a reference point."""
        return interpolate_track(self, ref_lat, ref_lon, ref_h, step_s)

    def track_ecef(self, step_s: float = 1.0) -> Track:
        """Uniform-step track in ECEF meters with absolute Unix times; frame-free for scoring."""
        xyz = geodesy.geodetic_to_ecef(self.lat, self.lon, self.height_m).reshape(-1, 3)
        t0, t1 = self.span
        if t1 - t0 < step_s:
            raise ValueError("flight too short to interpolate")
        t = uniform_times(t0, t1, step_s)
        samples = PchipInterpolator(self.time_unix, xyz, axis=0, extrapolate=False)(t)
        return Track(t, samples, icao=self.icao, type_code=self.type_code)


def split_legs(
    time_unix: FloatArray,
    flags: NDArray[np.int16],
    on_ground: NDArray[np.bool_],
    gap_s: float = 60.0,
) -> list[tuple[int, int]]:
    """Index ranges [start, stop) of legs."""
    n = time_unix.shape[0]
    if n == 0:
        return []
    cut = np.zeros(n, dtype=bool)
    cut[1:] |= np.diff(time_unix) > gap_s
    cut |= (flags & FLAG_NEW_LEG) != 0
    cut[1:] |= on_ground[:-1] & ~on_ground[1:]
    starts = np.flatnonzero(cut).tolist()
    bounds = [0, *starts, n]
    return [(a, b) for a, b in itertools.pairwise(bounds) if b > a]


def ellipsoidal_heights(
    alt_geom_ft: NDArray[np.float32], alt_baro_ft: NDArray[np.float32]
) -> tuple[FloatArray, str]:
    """Height above the ellipsoid per point and how it was obtained."""
    geom = np.asarray(alt_geom_ft, dtype=float) * FT_TO_M
    baro = np.asarray(alt_baro_ft, dtype=float) * FT_TO_M
    have_geom = ~np.isnan(geom)
    have_baro = ~np.isnan(baro)
    if have_geom.all():
        return geom, "geometric"
    out = geom.copy()
    both = have_geom & have_baro
    if both.any():
        offset = float(np.median(geom[both] - baro[both]))
        fill = ~have_geom & have_baro
        out[fill] = baro[fill] + offset
        source = "geometric" if not fill.any() else "baro_shifted"
    else:
        out[~have_geom] = baro[~have_geom]
        source = "baro_uncorrected"
    if np.isnan(out).any():
        good = ~np.isnan(out)
        if good.any():
            out = np.interp(np.arange(out.size), np.flatnonzero(good), out[good])
        else:
            out = np.zeros_like(out)
    return out, source


def _column(table: pa.Table, name: str, dtype: type = float) -> np.ndarray:
    return table.column(name).to_numpy(zero_copy_only=False).astype(dtype)


def _strings(table: pa.Table, name: str) -> tuple[NDArray[np.int64], list[str]]:
    """Dictionary codes (-1 where null) and the distinct values of a string column.

    One Python string per distinct value rather than per row: a continental day has 60 million
    rows, and building a string object for each took most of the preparation time and tens of
    gigabytes.
    """
    enc = pc.dictionary_encode(table.column(name).combine_chunks())
    codes = np.asarray(enc.indices.fill_null(-1).to_numpy(zero_copy_only=False), dtype=np.int64)
    words = ["" if w is None else str(w) for w in enc.dictionary.to_pylist()]
    return codes, words


def prepare_flights(
    table: pa.Table,
    types: TypeTable | None = None,
    min_points: int = 5,
    gap_s: float = 60.0,
    airborne_only: bool = True,
) -> list[Flight]:
    """Flights from a cached day table (see `earwitness.data.adsb_lol`)."""
    if table.num_rows == 0:
        return []
    icao_codes, icao_words = _strings(table, "icao")
    # aircraft in alphabetical order, as a sort on the strings themselves would give
    rank = np.empty(len(icao_words), dtype=np.int64)
    rank[np.argsort(np.asarray(icao_words, dtype=object), kind="stable")] = np.arange(rank.size)
    icao_key = np.full(icao_codes.shape, -1, dtype=np.int64)
    icao_key[icao_codes >= 0] = rank[icao_codes[icao_codes >= 0]]
    t = np.asarray(table.column("time").to_numpy()).astype("datetime64[ns]").astype("int64") / 1e9
    order = np.lexsort((t, icao_key))
    icao_key = icao_key[order]
    icao_codes = icao_codes[order]
    t = t[order]
    lat = _column(table, "lat")[order]
    lon = _column(table, "lon")[order]
    alt_baro = _column(table, "alt_baro_ft", np.float32)[order]
    alt_geom = _column(table, "alt_geom_ft", np.float32)[order]
    on_ground = _column(table, "on_ground", bool)[order]
    gs = _column(table, "gs_kt", np.float32)[order] * KT_TO_MPS
    flags = _column(table, "flags", np.int16)[order]
    type_codes, type_words = _strings(table, "type_code")
    desc_codes, desc_words = _strings(table, "description")
    reg_codes, reg_words = _strings(table, "registration")
    type_codes, desc_codes, reg_codes = type_codes[order], desc_codes[order], reg_codes[order]

    def word(words: list[str], codes: NDArray[np.int64], i: int) -> str:
        c = int(codes[i])
        return words[c] if c >= 0 else ""

    flights: list[Flight] = []
    boundaries = np.flatnonzero(np.r_[True, icao_key[1:] != icao_key[:-1], True])
    for a, b in itertools.pairwise(boundaries):
        code = word(type_words, type_codes, a)
        klass = classify(code, types)
        for leg_no, (s, e) in enumerate(split_legs(t[a:b], flags[a:b], on_ground[a:b], gap_s)):
            sl = slice(a + s, a + e)
            keep = ~on_ground[sl] if airborne_only else np.ones(e - s, dtype=bool)
            if np.count_nonzero(keep) < min_points:
                continue
            idx = np.flatnonzero(keep) + a + s
            # drop exact duplicate timestamps, which break interpolation
            tt = t[idx]
            uniq = np.r_[True, np.diff(tt) > 0]
            idx = idx[uniq]
            if idx.size < min_points:
                continue
            heights, source = ellipsoidal_heights(alt_geom[idx], alt_baro[idx])
            speed = gs[idx]
            if np.isnan(speed).any():
                good = ~np.isnan(speed)
                speed = (
                    np.interp(np.arange(speed.size), np.flatnonzero(good), speed[good])
                    if good.any()
                    else np.zeros_like(speed)
                )
            flights.append(
                Flight(
                    icao=word(icao_words, icao_codes, a),
                    leg=leg_no,
                    type_code=code,
                    description=word(desc_words, desc_codes, a),
                    engine_class=klass,
                    time_unix=t[idx],
                    lat=lat[idx],
                    lon=lon[idx],
                    height_m=heights,
                    gs_mps=speed,
                    height_source=source,
                    registration=word(reg_words, reg_codes, a),
                )
            )
    return flights


def interpolate_track(
    flight: Flight, ref_lat: float, ref_lon: float, ref_h: float = 0.0, step_s: float = 1.0
) -> Track:
    """Resample a flight to a uniform step in ENU about a reference point."""
    enu = geodesy.geodetic_to_enu(flight.lat, flight.lon, flight.height_m, ref_lat, ref_lon, ref_h)
    t0, t1 = flight.span
    if t1 - t0 < step_s:
        raise ValueError("flight too short to interpolate")
    t = uniform_times(t0, t1, step_s)
    if flight.time_unix.size >= 2:
        interp = PchipInterpolator(flight.time_unix, enu, axis=0, extrapolate=False)
        samples = interp(t)
    else:
        samples = np.repeat(enu[:1], t.size, axis=0)
    return Track(t, samples, icao=flight.icao, type_code=flight.type_code)


def flights_from_tracks_json(path: str | Path, engine_class: str = "piston") -> list[Flight]:
    """Flights from a simulator `tracks.json` (one trace per synthetic aircraft at 1 s)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    flights: list[Flight] = []
    for ac in data["aircraft"]:
        pts = ac["points"]
        t = np.array([p["t"] for p in pts], dtype=float)
        flights.append(
            Flight(
                icao=str(ac.get("icao", "")),
                leg=0,
                type_code=str(ac.get("type", "")),
                description="",
                engine_class=str(ac.get("engine_class_name", engine_class)),
                time_unix=t,
                lat=np.array([p["lat"] for p in pts], dtype=float),
                lon=np.array([p["lon"] for p in pts], dtype=float),
                height_m=np.array([p["alt_m"] for p in pts], dtype=float),
                gs_mps=np.array([p["gs_mps"] for p in pts], dtype=float),
                height_source="geometric",
            )
        )
    return flights


def flights_summary(flights: list[Flight]) -> dict[str, int]:
    """Counts by engine class."""
    out: dict[str, int] = {}
    for f in flights:
        out[f.engine_class] = out.get(f.engine_class, 0) + 1
    return out
