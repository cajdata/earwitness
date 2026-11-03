"""Candidate funnel for the aircraft method, ordered by selectivity.

Speed gate: a flight can only be an event's source where its speed sits in the event's range.
Distance tube: candidates lie on a lattice where the flight's closest-approach slant distance is
inside the event's range, with a hard cap. Timing pair: a candidate must sit in an event-1 tube
and an event-2 tube whose heard closest-approach times differ by the observed gap. Silence
filter: a candidate that would have heard another propeller aircraft during a window the
recording shows as silent is dropped (or penalized). Single-event mode replaces the pair with
the known recording time.

Everything geometric runs in ECEF so no tangent plane has to cover the search region; the only
planar structure is a lat/lon lattice with metric spacing whose cell ids make the spatial join a
sorted-array intersection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import cKDTree

from earwitness.aircraft.model import Track
from earwitness.aircraft.prep import Flight
from earwitness.audio.events import FlyoverEvent, Range, SilenceWindow
from earwitness.core import geodesy
from earwitness.core.grid import BBox, ellipsoid_radii
from earwitness.core.physics import SPEED_OF_SOUND

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
COLS = 1 << 24


@dataclass(frozen=True)
class FunnelConfig:
    """Funnel parameters (defaults per the handoff)."""

    grid_spacing_m: float = 200.0
    max_distance_m: float = 10000.0
    """Hard cap on closest-approach slant distance."""
    timing_tolerance_s: float = 5.0
    silence_distance_m: float = 4000.0
    silence_mode: str = "hard"
    """'hard' drops candidates with silence hits, 'soft' keeps them with a count, 'off' skips."""
    keep_classes: tuple[str, ...] = ("piston", "turboprop", "unknown")
    speed_of_sound: float = SPEED_OF_SOUND
    index_step_s: float = 5.0
    """Sample step of the spatial index used by the pair prefilter and the silence filter."""
    min_speed_mps: float = 20.0
    """Lower bound on speed used when converting distances to time slack."""
    max_pairs: int = 20000


class Lattice:
    """Regular latitude/longitude lattice with metric spacing and stable integer cell ids."""

    def __init__(self, bbox: BBox, spacing_m: float) -> None:
        self.bbox = bbox
        self.spacing = spacing_m
        meridional, _ = ellipsoid_radii(bbox.center[0])
        self.dlat = float(np.rad2deg(spacing_m / meridional))
        self.lat0 = bbox.south
        self.lon0 = bbox.west

    def _dlon(self, row: IntArray) -> FloatArray:
        lat = np.deg2rad(self.lat0 + (row + 0.5) * self.dlat)
        n = geodesy.WGS84_A / np.sqrt(1.0 - geodesy.WGS84_E2 * np.sin(lat) ** 2)
        return np.rad2deg(self.spacing / (n * np.maximum(np.cos(lat), 1e-6)))

    def cell(self, lat: FloatArray, lon: FloatArray) -> IntArray:
        """Cell id of each point."""
        row = np.floor((np.asarray(lat, dtype=float) - self.lat0) / self.dlat).astype(np.int64)
        col = np.floor((np.asarray(lon, dtype=float) - self.lon0) / self._dlon(row)).astype(
            np.int64
        )
        return row * COLS + col

    def center(self, ids: IntArray) -> tuple[FloatArray, FloatArray]:
        """(lat, lon) of cell centers."""
        ids = np.asarray(ids, dtype=np.int64)
        row, col = ids // COLS, ids % COLS
        lat = self.lat0 + (row + 0.5) * self.dlat
        lon = self.lon0 + (col + 0.5) * self._dlon(row)
        return lat, lon


def cpa_to_points(
    track_xyz: FloatArray, track_t: FloatArray, points_xyz: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Closest-approach time and slant distance from a sampled track to many points.

    Uses the squared distance at the nearest sample and its neighbors with a parabolic fit, which
    is exact for straight uniform motion and within centimeters for turning aircraft at 1 s steps.
    """
    pts = np.atleast_2d(points_xyz)
    d2 = ((pts[:, None, :] - track_xyz[None, :, :]) ** 2).sum(axis=2)
    i = np.argmin(d2, axis=1)
    n = track_xyz.shape[0]
    im = np.clip(i - 1, 0, n - 1)
    ip = np.clip(i + 1, 0, n - 1)
    rows = np.arange(pts.shape[0])
    dm, d0, dp = d2[rows, im], d2[rows, i], d2[rows, ip]
    denom = dm - 2.0 * d0 + dp
    interior = (i > 0) & (i < n - 1) & (denom > 1e-9)
    delta = np.where(interior, 0.5 * (dm - dp) / np.where(denom > 0, denom, 1.0), 0.0)
    delta = np.clip(delta, -1.0, 1.0)
    dt = np.where(interior, track_t[ip] - track_t[i], 0.0)
    t_cpa = track_t[i] + delta * dt
    d2_min = np.where(interior, d0 - 0.125 * (dm - dp) ** 2 / np.where(denom > 0, denom, 1.0), d0)
    return t_cpa, np.sqrt(np.maximum(d2_min, 0.0))


@dataclass
class FlightSet:
    """Flights admitted to the funnel, with a coarse spatial-temporal index and lazy 1 s tracks."""

    flights: list[Flight]
    ref_lat: float
    ref_lon: float
    sample_xy: FloatArray
    sample_t: FloatArray
    sample_flight: IntArray
    sample_speed: FloatArray
    _tracks: dict[int, Track] = field(default_factory=dict, repr=False)
    _tree: cKDTree | None = field(default=None, repr=False)

    @classmethod
    def build(
        cls,
        flights: list[Flight],
        bbox: BBox,
        cfg: FunnelConfig,
        t_start: float | None = None,
        t_end: float | None = None,
    ) -> FlightSet:
        """Keep flights of the admitted classes that touch the time window; index every few s."""
        ref_lat, ref_lon = bbox.center
        kept: list[Flight] = []
        xs, ts, fs, vs = [], [], [], []
        for fl in flights:
            if fl.engine_class not in cfg.keep_classes:
                continue
            a, b = fl.span
            if (t_end is not None and a > t_end) or (t_start is not None and b < t_start):
                continue
            idx = len(kept)
            kept.append(fl)
            t = np.arange(a, b + 1e-9, cfg.index_step_s)
            lat = np.interp(t, fl.time_unix, fl.lat)
            lon = np.interp(t, fl.time_unix, fl.lon)
            enu = geodesy.geodetic_to_enu(lat, lon, 0.0, ref_lat, ref_lon, 0.0)
            xs.append(enu[:, :2])
            ts.append(t)
            fs.append(np.full(t.size, idx, dtype=np.int64))
            vs.append(np.interp(t, fl.time_unix, fl.gs_mps))
        if not kept:
            empty = np.zeros((0, 2))
            return cls(
                kept, ref_lat, ref_lon, empty, np.zeros(0), np.zeros(0, np.int64), np.zeros(0)
            )
        return cls(
            kept,
            ref_lat,
            ref_lon,
            np.concatenate(xs),
            np.concatenate(ts),
            np.concatenate(fs),
            np.concatenate(vs),
        )

    def __len__(self) -> int:
        return len(self.flights)

    def track(self, idx: int) -> Track:
        """ECEF track at 1 s for a flight, cached."""
        if idx not in self._tracks:
            self._tracks[idx] = self.flights[idx].track_ecef(1.0)
        return self._tracks[idx]

    def tree(self) -> cKDTree:
        """2D tree over indexed samples in the regional tangent plane."""
        if self._tree is None:
            self._tree = cKDTree(self.sample_xy)
        return self._tree

    def xy(self, lat: FloatArray, lon: FloatArray) -> FloatArray:
        """Regional tangent-plane coordinates of points."""
        enu = geodesy.geodetic_to_enu(lat, lon, 0.0, self.ref_lat, self.ref_lon, 0.0)
        return enu[..., :2]


def speed_mask(fs: FlightSet, event: FlyoverEvent, margin: float = 0.0) -> NDArray[np.bool_]:
    """Indexed samples whose speed falls in the event's range."""
    return (fs.sample_speed >= event.v_mps.lo - margin) & (
        fs.sample_speed <= event.v_mps.hi + margin
    )


@dataclass
class TubeCells:
    """Lattice cells inside one flight's distance tube for one event."""

    flight: int
    ids: IntArray
    lat: FloatArray
    lon: FloatArray
    heard: FloatArray
    """Heard closest-approach time, Unix seconds."""
    slant: FloatArray


def tube_cells(
    fs: FlightSet,
    flight_idx: int,
    event: FlyoverEvent,
    lattice: Lattice,
    cfg: FunnelConfig,
    ground_height: GroundHeight | None = None,
    near_xy: FloatArray | None = None,
    near_radius_m: float | None = None,
    t_window: tuple[float, float] | None = None,
) -> TubeCells:
    """Cells whose closest-approach slant distance to the flight lies in the event's range.

    Candidate points are laid across the track at the lattice spacing, between the lateral
    offsets that the distance range allows given the aircraft's height above ground, then
    snapped to the lattice and checked with the exact closest approach of the 1 s track.
    """
    fl = fs.flights[flight_idx]
    track = fs.track(flight_idx)
    d_hi = min(event.d_m.hi, cfg.max_distance_m)
    d_lo = event.d_m.lo
    empty = TubeCells(
        flight_idx, np.zeros(0, np.int64), np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0)
    )
    if d_hi <= 0.0:
        return empty
    t = track.t
    lat = np.interp(t, fl.time_unix, fl.lat)
    lon = np.interp(t, fl.time_unix, fl.lon)
    h_ac = np.interp(t, fl.time_unix, fl.height_m)
    speed = np.interp(t, fl.time_unix, fl.gs_mps)
    keep = (speed >= event.v_mps.lo) & (speed <= event.v_mps.hi)
    if t_window is not None:
        keep &= (t >= t_window[0]) & (t <= t_window[1])
    if near_xy is not None and near_radius_m is not None:
        xy = fs.xy(lat, lon)
        keep &= np.hypot(xy[:, 0] - near_xy[0], xy[:, 1] - near_xy[1]) <= near_radius_m
    idx = np.flatnonzero(keep)
    if idx.size == 0:
        return empty
    stride = max(1, round(cfg.grid_spacing_m / max(float(np.median(speed[idx])), 1.0)))
    idx = idx[::stride]
    # unit lateral vector from the local heading
    enu = geodesy.geodetic_to_enu(
        lat[idx], lon[idx], 0.0, float(lat[idx[0]]), float(lon[idx[0]]), 0.0
    )
    if idx.size >= 2:
        de = np.gradient(enu[:, 0])
        dn = np.gradient(enu[:, 1])
    else:
        de, dn = np.ones(1), np.zeros(1)
    norm = np.maximum(np.hypot(de, dn), 1e-9)
    ne, nn = -dn / norm, de / norm
    h_ground = (
        ground_height(lat[idx], lon[idx]) if ground_height is not None else np.zeros(idx.size)
    )
    dh = np.maximum(h_ac[idx] - h_ground, 0.0)
    x_hi = np.sqrt(np.maximum(d_hi**2 - dh**2, 0.0))
    x_lo = np.sqrt(np.maximum(d_lo**2 - dh**2, 0.0))
    offsets = np.arange(0.0, d_hi + cfg.grid_spacing_m, cfg.grid_spacing_m)
    offsets = np.concatenate([-offsets[::-1], offsets[1:]])
    xo = offsets[None, :]
    valid = (np.abs(xo) >= x_lo[:, None] - cfg.grid_spacing_m) & (
        np.abs(xo) <= x_hi[:, None] + cfg.grid_spacing_m
    )
    if not valid.any():
        return empty
    meridional, _ = ellipsoid_radii(float(np.mean(lat[idx])))
    prime = geodesy.WGS84_A / np.sqrt(1.0 - geodesy.WGS84_E2 * np.sin(np.deg2rad(lat[idx])) ** 2)
    dlat = np.rad2deg((xo * nn[:, None]) / meridional)
    dlon = np.rad2deg((xo * ne[:, None]) / (prime[:, None] * np.cos(np.deg2rad(lat[idx]))[:, None]))
    plat = (lat[idx][:, None] + dlat)[valid]
    plon = (lon[idx][:, None] + dlon)[valid]
    ids = np.unique(lattice.cell(plat, plon))
    clat, clon = lattice.center(ids)
    inside = lattice.bbox.contains(clat, clon)
    ids, clat, clon = ids[inside], clat[inside], clon[inside]
    if ids.size == 0:
        return empty
    cg = ground_height(clat, clon) if ground_height is not None else np.zeros(ids.size)
    pts = geodesy.geodetic_to_ecef(clat, clon, cg)
    t_cpa, slant = cpa_to_points(track.enu, track.t, pts)
    ok = (slant >= d_lo) & (slant <= d_hi)
    heard = t_cpa + slant / cfg.speed_of_sound
    return TubeCells(flight_idx, ids[ok], clat[ok], clon[ok], heard[ok], slant[ok])


class GroundHeight:
    """Callable giving ground height above the ellipsoid; flat zero or terrain-backed."""

    def __init__(self, terrain: object | None = None) -> None:
        self._terrain = terrain

    def __call__(self, lat: FloatArray, lon: FloatArray) -> FloatArray:
        if self._terrain is None:
            return np.zeros(np.asarray(lat).shape, dtype=float)
        return np.asarray(self._terrain.ellipsoidal_height(lat, lon), dtype=float)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class PairHint:
    """Two flights whose paths meet in space and in the right time order, and where."""

    f1: int
    f2: int
    xy: FloatArray


def pair_prefilter(
    fs: FlightSet, e1: FlyoverEvent, e2: FlyoverEvent, gap: Range, cfg: FunnelConfig
) -> list[PairHint]:
    """Flight pairs that could produce two flyovers `gap` apart at one point.

    Event-1 samples are indexed in (x, y, scaled time) with time shifted by the gap, so a ball
    query around each event-2 sample finds samples that are close in space and whose times
    differ by the gap within the allowed slack.
    """
    m1 = speed_mask(fs, e1)
    m2 = speed_mask(fs, e2)
    if not m1.any() or not m2.any():
        return []
    reach = 2.0 * min(max(e1.d_m.hi, e2.d_m.hi), cfg.max_distance_m) + cfg.grid_spacing_m
    v_min = max(min(e1.v_mps.lo, e2.v_mps.lo), cfg.min_speed_mps)
    slack = cfg.timing_tolerance_s + gap.half_width + reach / v_min + cfg.index_step_s
    scale = reach / slack
    i1 = np.flatnonzero(m1)
    i2 = np.flatnonzero(m2)
    pts1 = np.column_stack([fs.sample_xy[i1], (fs.sample_t[i1] + gap.mid) * scale])
    pts2 = np.column_stack([fs.sample_xy[i2], fs.sample_t[i2] * scale])
    tree = cKDTree(pts1)
    hits = tree.query_ball_point(pts2, reach * np.sqrt(2.0))
    pairs: dict[tuple[int, int], FloatArray] = {}
    for q, lst in enumerate(hits):
        if not lst:
            continue
        f2 = int(fs.sample_flight[i2[q]])
        for h in lst:
            f1 = int(fs.sample_flight[i1[h]])
            if f1 == f2:
                continue
            key = (f1, f2)
            if key not in pairs:
                pairs[key] = 0.5 * (fs.sample_xy[i1[h]] + fs.sample_xy[i2[q]])
                if len(pairs) >= cfg.max_pairs:
                    return [PairHint(a, b, xy) for (a, b), xy in pairs.items()]
    return [PairHint(a, b, xy) for (a, b), xy in pairs.items()]


@dataclass
class Candidates:
    """Candidate points with their source flights and the recording start they imply."""

    cell: IntArray
    lat: FloatArray
    lon: FloatArray
    f1: IntArray
    f2: IntArray
    """-1 in single-event mode."""
    heard1: FloatArray
    heard2: FloatArray
    slant1: FloatArray
    slant2: FloatArray
    t_start: FloatArray
    """Implied recording start, Unix seconds."""
    silence_hits: IntArray = field(default_factory=lambda: np.zeros(0, np.int64))

    def __len__(self) -> int:
        return int(self.cell.shape[0])

    def subset(self, idx: NDArray) -> Candidates:
        """Rows by index or mask."""
        hits = self.silence_hits[idx] if self.silence_hits.size == len(self) else self.silence_hits
        return Candidates(
            self.cell[idx], self.lat[idx], self.lon[idx], self.f1[idx], self.f2[idx],
            self.heard1[idx], self.heard2[idx], self.slant1[idx], self.slant2[idx],
            self.t_start[idx], hits,
        )  # fmt: skip

    @staticmethod
    def concat(parts: list[Candidates]) -> Candidates:
        """Stack candidate sets."""
        if not parts:
            return Candidates.empty()
        return Candidates(*[np.concatenate([getattr(p, name) for p in parts]) for name in (
            "cell", "lat", "lon", "f1", "f2", "heard1", "heard2", "slant1", "slant2", "t_start"
        )])  # fmt: skip

    @staticmethod
    def empty() -> Candidates:
        """No candidates."""
        z = np.zeros(0)
        zi = np.zeros(0, np.int64)
        return Candidates(zi, z, z, zi, zi, z, z, z, z, z)


def timing_join(
    t1: TubeCells, t2: TubeCells, e1: FlyoverEvent, gap: Range, cfg: FunnelConfig
) -> Candidates:
    """Cells in both tubes whose heard closest-approach times differ by the gap."""
    common, i1, i2 = np.intersect1d(t1.ids, t2.ids, return_indices=True)
    if common.size == 0:
        return Candidates.empty()
    diff = t2.heard[i2] - t1.heard[i1]
    ok = np.abs(diff - gap.mid) <= gap.half_width + cfg.timing_tolerance_s
    if not ok.any():
        return Candidates.empty()
    i1, i2 = i1[ok], i2[ok]
    n = i1.size
    return Candidates(
        cell=common[ok],
        lat=t1.lat[i1],
        lon=t1.lon[i1],
        f1=np.full(n, t1.flight, dtype=np.int64),
        f2=np.full(n, t2.flight, dtype=np.int64),
        heard1=t1.heard[i1],
        heard2=t2.heard[i2],
        slant1=t1.slant[i1],
        slant2=t2.slant[i2],
        t_start=t1.heard[i1] - e1.t0_s.mid,
    )


def two_event_candidates(
    fs: FlightSet,
    e1: FlyoverEvent,
    e2: FlyoverEvent,
    gap: Range,
    lattice: Lattice,
    cfg: FunnelConfig,
    ground_height: GroundHeight | None = None,
    progress: object | None = None,
) -> tuple[Candidates, int]:
    """Speed gate, pair prefilter, distance tubes and timing join. Returns (candidates, pairs)."""
    hints = pair_prefilter(fs, e1, e2, gap, cfg)
    reach = 2.0 * min(max(e1.d_m.hi, e2.d_m.hi), cfg.max_distance_m) + cfg.grid_spacing_m
    parts: list[Candidates] = []
    for k, hint in enumerate(hints, start=1):
        tube1 = tube_cells(fs, hint.f1, e1, lattice, cfg, ground_height, hint.xy, reach * 2.0)
        if tube1.ids.size == 0:
            continue
        tube2 = tube_cells(fs, hint.f2, e2, lattice, cfg, ground_height, hint.xy, reach * 2.0)
        if tube2.ids.size == 0:
            continue
        joined = timing_join(tube1, tube2, e1, gap, cfg)
        if len(joined):
            parts.append(joined)
        if progress is not None and k % 500 == 0:
            progress(
                f"funnel: {k}/{len(hints)} flight pairs, {sum(len(p) for p in parts)} candidates"
            )
    return Candidates.concat(parts), len(hints)


def single_event_candidates(
    fs: FlightSet,
    event: FlyoverEvent,
    t_known: float,
    tolerance_s: float,
    lattice: Lattice,
    cfg: FunnelConfig,
    ground_height: GroundHeight | None = None,
) -> Candidates:
    """Known-time mode: tube cells whose heard closest approach matches the known clock."""
    d_hi = min(event.d_m.hi, cfg.max_distance_m)
    v_min = max(event.v_mps.lo, cfg.min_speed_mps)
    slack = tolerance_s + event.t0_s.half_width + d_hi / v_min + cfg.index_step_s
    lo = t_known + event.t0_s.lo - slack
    hi = t_known + event.t0_s.hi + slack
    m = speed_mask(fs, event) & (fs.sample_t >= lo) & (fs.sample_t <= hi)
    parts: list[Candidates] = []
    for f in np.unique(fs.sample_flight[m]):
        tube = tube_cells(fs, int(f), event, lattice, cfg, ground_height, t_window=(lo, hi))
        if tube.ids.size == 0:
            continue
        ok = np.abs(tube.heard - (t_known + event.t0_s.mid)) <= event.t0_s.half_width + tolerance_s
        if not ok.any():
            continue
        n = int(ok.sum())
        parts.append(
            Candidates(
                cell=tube.ids[ok],
                lat=tube.lat[ok],
                lon=tube.lon[ok],
                f1=np.full(n, int(f), dtype=np.int64),
                f2=np.full(n, -1, dtype=np.int64),
                heard1=tube.heard[ok],
                heard2=np.full(n, np.nan),
                slant1=tube.slant[ok],
                slant2=np.full(n, np.nan),
                t_start=np.full(n, t_known),
            )
        )
    return Candidates.concat(parts)


def silence_hits(
    fs: FlightSet, cand: Candidates, windows: list[SilenceWindow], cfg: FunnelConfig
) -> IntArray:
    """Distinct flights within the silence distance of each candidate during its silent windows.

    A candidate's own event flights are not counted: the user marked when they were heard, and a
    few seconds of the same aircraft lingering at the edge of that window is not a contradiction.
    """
    if not windows or len(cand) == 0 or len(fs) == 0:
        return np.zeros(len(cand), dtype=np.int64)
    xy = fs.xy(cand.lat, cand.lon)
    lists = fs.tree().query_ball_point(xy, cfg.silence_distance_m)
    out = np.zeros(len(cand), dtype=np.int64)
    for i, hits in enumerate(lists):
        if not hits:
            continue
        hits = np.asarray(hits, dtype=np.int64)
        rel = fs.sample_t[hits] - cand.t_start[i]
        inside = np.zeros(hits.size, dtype=bool)
        for w in windows:
            inside |= (rel >= w.start_s) & (rel <= w.end_s)
        if inside.any():
            others = np.unique(fs.sample_flight[hits[inside]])
            own = {int(cand.f1[i]), int(cand.f2[i])}
            out[i] = sum(1 for f in others if int(f) not in own)
    return out
