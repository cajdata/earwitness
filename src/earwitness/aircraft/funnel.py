"""Candidate funnel for the aircraft method, ordered by selectivity.

Speed gate: a flight can only be an event's source where its speed sits in the event's range.
Distance tube: candidates lie on a lattice where the flight's closest-approach slant distance is
inside the event's range, with a hard cap. Timing pair: a candidate must sit in an event-1 tube
and an event-2 tube whose heard closest-approach times differ by the observed gap. Silence
filter: a candidate that would have heard another propeller aircraft (slant range under the
silence distance, at arrival time) during a window the recording shows as silent is dropped, or
penalized in soft mode. Single-event mode replaces the pair with the known recording time.

Everything geometric runs in ECEF so no tangent plane has to cover the search region; the only
planar structure is a lat/lon lattice with metric spacing whose cell ids make the spatial join a
sorted-array intersection.
"""

from __future__ import annotations

import itertools
from collections import OrderedDict
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
MAX_CACHED_TRACKS = 4096
"""1 s tracks kept in memory; a continental day touches tens of thousands of flights."""
PREFILTER_CHUNK = 20000
"""Event-2 samples per ball query in the pair prefilter; bounds the hit lists in memory."""
SILENCE_CHUNK = 2000
"""Candidates per ball query in the silence filter; bounds the hit lists in memory."""


@dataclass(frozen=True)
class FunnelConfig:
    """Funnel parameters (defaults per the handoff)."""

    grid_spacing_m: float = 200.0
    max_distance_m: float = 10000.0
    """Hard cap on closest-approach slant distance."""
    timing_tolerance_s: float = 5.0
    silence_distance_m: float = 4000.0
    silence_mode: str = "hard"
    """'hard' drops candidates with silence hits, 'soft' subtracts `silence_penalty_db` per
    offending flight from the score, 'off' skips the filter."""
    silence_penalty_db: float = 3.0
    keep_classes: tuple[str, ...] = ("piston", "turboprop", "unknown")
    speed_of_sound: float = SPEED_OF_SOUND
    index_step_s: float = 5.0
    """Sample step of the spatial index used by the pair prefilter and the silence filter."""
    min_speed_mps: float = 20.0
    """Lower bound on speed used when converting distances to time slack."""
    max_pairs: int = 100000
    """Most pair hints the prefilter returns; the run log says when the cap truncated it."""
    prefilter: str = "start"
    """'start' pairs samples by the recording start each one implies at its own speed, a time
    slack of the events' narrowed widths; 'gap' pairs them by the heard-time gap with the slack
    a sample's distance from closest approach allows (the M4 prefilter: a superset of the
    hints the funnel can use, at tens of times the sample pairs with honest speed ranges)."""


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


def _sqdist(pts: FloatArray, xyz: FloatArray) -> FloatArray:
    return ((pts - xyz) ** 2).sum(axis=-1)


def refine_cpa(
    track_xyz: FloatArray, track_t: FloatArray, pts: FloatArray, i: IntArray
) -> tuple[FloatArray, FloatArray]:
    """Closest-approach time and slant from the nearest sample `i` of each point and its
    neighbors, with a parabolic fit: exact for straight uniform motion and within centimeters
    for turning aircraft at 1 s steps. A sample at a track end, or beside a gap left when the
    track was cut down, answers as it is."""
    n = track_xyz.shape[0]
    i = np.asarray(i, dtype=np.int64)
    im = np.clip(i - 1, 0, n - 1)
    ip = np.clip(i + 1, 0, n - 1)
    dm = _sqdist(pts, track_xyz[im])
    d0 = _sqdist(pts, track_xyz[i])
    dp = _sqdist(pts, track_xyz[ip])
    denom = dm - 2.0 * d0 + dp
    # the fit needs the nearest sample's true neighbors, not the far side of a gap
    step = float(np.median(np.diff(track_t))) if n > 1 else 1.0
    adjacent = (track_t[ip] - track_t[i] <= 1.5 * step) & (track_t[i] - track_t[im] <= 1.5 * step)
    interior = (i > 0) & (i < n - 1) & (denom > 1e-9) & adjacent
    delta = np.where(interior, 0.5 * (dm - dp) / np.where(denom > 0, denom, 1.0), 0.0)
    delta = np.clip(delta, -1.0, 1.0)
    dt = np.where(interior, track_t[ip] - track_t[i], 0.0)
    t_cpa = track_t[i] + delta * dt
    d2_min = np.where(interior, d0 - 0.125 * (dm - dp) ** 2 / np.where(denom > 0, denom, 1.0), d0)
    return t_cpa, np.sqrt(np.maximum(d2_min, 0.0))


def cpa_to_points(
    track_xyz: FloatArray, track_t: FloatArray, points_xyz: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Closest-approach time and slant distance from a sampled track to many points.

    The track's nearest sample to each point, refined with `refine_cpa`. The nearest sample comes
    from a k-d tree over the track, so memory grows with the number of points rather than points
    times samples: the dense distance matrix of a loitering track against a wide tube ran to tens
    of gigabytes at continental scale. A track that passes a point more than once answers with
    its nearest pass only; the funnel uses `descend_to_pass` and `heard_pass` instead.
    """
    pts = np.atleast_2d(np.asarray(points_xyz, dtype=float))
    n = track_xyz.shape[0]
    if n == 0:
        raise ValueError("cpa_to_points needs at least one track sample")
    if pts.shape[0] == 0:
        return np.zeros(0), np.zeros(0)
    i = np.asarray(cKDTree(track_xyz).query(pts, k=1)[1], dtype=np.int64)
    return refine_cpa(track_xyz, track_t, pts, i)


PASS_STRIDE = 4
"""Track samples per step of the coarse walk and search for a pass (`descend_to_pass`,
`heard_pass`) before the 1 s polish. Two passes of one point lie tens of seconds apart at the
least, while ADS-B jitter on a 1 s track dents the distance with minima a few seconds apart,
which a walk one sample at a time can stop at well short of the pass's closest approach."""


def _walk(track_xyz: FloatArray, pts: FloatArray, j: IntArray, step: int) -> IntArray:
    """Move each index `step` samples at a time toward a nearer track sample until neither
    neighbor `step` away is nearer (the ends clamp)."""
    n = track_xyz.shape[0]
    j = j.copy()
    d0 = _sqdist(pts, track_xyz[j])
    active = np.arange(j.size)
    while active.size:
        ja = j[active]
        pa = pts[active]
        jm = np.maximum(ja - step, 0)
        jp = np.minimum(ja + step, n - 1)
        dm = np.where(jm < ja, _sqdist(pa, track_xyz[jm]), np.inf)
        dp = np.where(jp > ja, _sqdist(pa, track_xyz[jp]), np.inf)
        cur = d0[active]
        down = (dm < cur) & (dm <= dp)
        up = (dp < cur) & ~down
        j[active[down]] = jm[down]
        j[active[up]] = jp[up]
        d0[active[down]] = dm[down]
        d0[active[up]] = dp[up]
        active = active[down | up]
    return j


def descend_to_pass(
    track_xyz: FloatArray,
    track_t: FloatArray,
    points_xyz: FloatArray,
    start: IntArray,
    stride: int = PASS_STRIDE,
) -> tuple[FloatArray, FloatArray, IntArray]:
    """Closest approach of each point on the pass through its own starting sample.

    From `start` (one track index per point) the walk moves `stride` samples at a time toward
    whichever neighbor is nearer the point until neither is, then one sample at a time, to a
    local minimum of the distance: the closest approach of that pass, refined with
    `refine_cpa`. A track that passes a place again and again, as pattern traffic does, has one
    minimum per pass, and the funnel starts each cell from the sample that laid it, so the cell
    keeps that pass rather than the track's nearest one. Returns (t_cpa, slant, index of the
    minimum).
    """
    pts = np.atleast_2d(np.asarray(points_xyz, dtype=float))
    n = track_xyz.shape[0]
    if n == 0:
        raise ValueError("descend_to_pass needs at least one track sample")
    if pts.shape[0] == 0:
        return np.zeros(0), np.zeros(0), np.zeros(0, np.int64)
    j = np.clip(np.asarray(start, dtype=np.int64), 0, n - 1)
    if stride > 1:
        j = _walk(track_xyz, pts, j, int(stride))
    j = _walk(track_xyz, pts, j, 1)
    t_cpa, slant = refine_cpa(track_xyz, track_t, pts, j)
    return t_cpa, slant, j


HEARD_PASS_BLOCK = 1 << 19
"""Point-sample pairs per block in `heard_pass`; bounds its distance matrices in memory."""


def heard_pass(
    track_xyz: FloatArray,
    track_t: FloatArray,
    points_xyz: FloatArray,
    heard_lo: FloatArray,
    heard_hi: FloatArray,
    c: float = SPEED_OF_SOUND,
    d_max: float = 10000.0,
    stride: int = PASS_STRIDE,
) -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
    """Closest approach of each point on the pass heard inside its own time window.

    For point i the track is searched for local minima of the distance whose sound arrives in
    [heard_lo[i], heard_hi[i]] (emission time plus slant over c), and the nearest such minimum
    is kept. The search looks at every `stride`-th sample over the window widened by a stride
    either side (at every sample it was most of the funnel's time, and it took jitter dents for
    passes), follows each minimum it finds to the 1 s samples (`descend_to_pass`) and checks
    the refined arrival against the window itself. A point whose
    window holds no minimum, because the aircraft only closed or only receded while its sound
    arrived then, has none (`found` False). Slants beyond `d_max` are not searched for: the
    emission times start d_max / c before each window. A track end counts as a minimum when the
    track moves away from the point there, as in `cpa_to_points`. Returns (t_cpa, slant, found).
    """
    pts = np.atleast_2d(np.asarray(points_xyz, dtype=float))
    m = pts.shape[0]
    t_out, s_out = np.zeros(m), np.zeros(m)
    found = np.zeros(m, dtype=bool)
    n = track_xyz.shape[0]
    if m == 0 or n == 0:
        return t_out, s_out, found
    stride = max(1, int(stride))
    lo = np.broadcast_to(np.asarray(heard_lo, dtype=float), (m,))
    hi = np.broadcast_to(np.asarray(heard_hi, dtype=float), (m,))
    step = float(np.median(np.diff(track_t))) if n > 1 else 1.0
    pad = stride * step
    # one coarse sample to spare on each side tells a minimum from the edge of the window
    a = np.maximum(np.searchsorted(track_t, lo - d_max / c - pad, side="left") - stride, 0)
    b = np.minimum(np.searchsorted(track_t, hi + pad, side="right") + stride, n)
    width = np.maximum((b - a + stride - 1) // stride, 0)
    w_max = int(width.max())
    if w_max <= 0:
        return t_out, s_out, found
    owner: list[IntArray] = []
    start: list[IntArray] = []
    rows = max(1, HEARD_PASS_BLOCK // w_max)
    for s0 in range(0, m, rows):
        sl = slice(s0, min(s0 + rows, m))
        w = int(width[sl].max())
        if w <= 0:
            continue
        idx = a[sl, None] + stride * np.arange(w)[None, :]
        valid = idx < b[sl, None]
        idx = np.minimum(idx, n - 1)
        d2 = np.where(valid, _sqdist(pts[sl, None, :], track_xyz[idx]), np.inf)
        # a minimum within reach: the samples to spare at either end of a window count only
        # where the track itself ends there, or a walk from them would follow the aircraft to
        # a pass far outside the window
        low = valid & (d2 <= (d_max + pad * 150.0) ** 2)
        low[:, 0] &= idx[:, 0] == 0
        if w > 1:
            low[:, 1:] &= d2[:, 1:] <= d2[:, :-1]
            low[:, :-1] &= d2[:, :-1] <= d2[:, 1:]
            last = np.minimum(width[sl] - 1, w - 1)
            r_all = np.arange(idx.shape[0])
            low[r_all, last] &= b[sl] == n
        r, k = np.nonzero(low)
        heard = track_t[idx[r, k]] + np.sqrt(d2[r, k]) / c
        keep = (heard >= lo[sl][r] - pad) & (heard <= hi[sl][r] + pad)
        owner.append(s0 + r[keep])
        start.append(idx[r[keep], k[keep]])
    if not owner or sum(o.size for o in owner) == 0:
        return t_out, s_out, found
    # follow every coarse minimum, then keep each point's nearest one heard inside its window
    who = np.concatenate(owner)
    t_cpa, slant, _ = descend_to_pass(track_xyz, track_t, pts[who], np.concatenate(start))
    heard = t_cpa + slant / c
    inside = (heard >= lo[who]) & (heard <= hi[who])
    who, t_cpa, slant = who[inside], t_cpa[inside], slant[inside]
    if who.size:
        order = np.lexsort((slant, who))
        who, t_cpa, slant = who[order], t_cpa[order], slant[order]
        first = np.ones(who.size, dtype=bool)
        first[1:] = who[1:] != who[:-1]
        who, t_cpa, slant = who[first], t_cpa[first], slant[first]
        t_out[who], s_out[who], found[who] = t_cpa, slant, True
    return t_out, s_out, found


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
    sample_h: FloatArray
    """Aircraft height above the ellipsoid at each indexed sample."""
    _tracks: OrderedDict[int, Track] = field(default_factory=OrderedDict, repr=False)
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
        xs, ts, fs, vs, hs = [], [], [], [], []
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
            hs.append(np.interp(t, fl.time_unix, fl.height_m))
        if not kept:
            empty = np.zeros((0, 2))
            zeros = np.zeros(0)
            return cls(kept, ref_lat, ref_lon, empty, zeros, np.zeros(0, np.int64), zeros, zeros)
        return cls(
            kept,
            ref_lat,
            ref_lon,
            np.concatenate(xs),
            np.concatenate(ts),
            np.concatenate(fs),
            np.concatenate(vs),
            np.concatenate(hs),
        )

    def __len__(self) -> int:
        return len(self.flights)

    def track(self, idx: int) -> Track:
        """ECEF track at 1 s for a flight, from a bounded most-recently-used cache."""
        if idx in self._tracks:
            self._tracks.move_to_end(idx)
        else:
            self._tracks[idx] = self.flights[idx].track_ecef(1.0)
            while len(self._tracks) > MAX_CACHED_TRACKS:
                self._tracks.popitem(last=False)
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
    """Lattice cells inside one flight's distance tube for one event, one row per cell and pass:
    a cell the flight passed more than once within range has a row for each pass."""

    flight: int
    ids: IntArray
    lat: FloatArray
    lon: FloatArray
    heard: FloatArray
    """Heard closest-approach time, Unix seconds."""
    slant: FloatArray
    height: FloatArray
    """Ground height above the ellipsoid at the cell center, m."""
    speed: FloatArray
    """Aircraft ground speed at closest approach, m/s."""


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
    snapped to the lattice. Each cell's closest approach is the one on the pass that laid it
    (`descend_to_pass` from the laying sample over the 1 s track), not the track's nearest
    sample: pattern traffic passes the same place again and again, at other distances and
    times, and only one of those passes was heard. A cell that several passes laid has a row
    for each of them.
    """
    fl = fs.flights[flight_idx]
    track = fs.track(flight_idx)
    d_hi = min(event.d_m.hi, cfg.max_distance_m)
    z = np.zeros(0)
    empty = TubeCells(flight_idx, np.zeros(0, np.int64), z, z, z, z, z, z)
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
    # the distance band at each sample's own speed: a profiled event ties d to v
    band = event.at_speed(speed[idx])["d_m"]
    d_lo_s, d_hi_s = band[0], np.minimum(band[1], cfg.max_distance_m)
    x_hi = np.sqrt(np.maximum(d_hi_s**2 - dh**2, 0.0))
    x_lo = np.sqrt(np.maximum(d_lo_s**2 - dh**2, 0.0))
    offsets = np.arange(0.0, float(x_hi.max()) + 2.0 * cfg.grid_spacing_m, cfg.grid_spacing_m)
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
    laid_by = idx[np.nonzero(valid)[0]]
    ids, inv = np.unique(lattice.cell(plat, plon), return_inverse=True)
    clat, clon = lattice.center(ids)
    inside = lattice.bbox.contains(clat, clon)
    if not inside.any():
        return empty
    cg = np.zeros(ids.size)
    if ground_height is not None:
        cg[inside] = ground_height(clat[inside], clon[inside])
    pts = geodesy.geodetic_to_ecef(clat, clon, cg)
    # one walk per cell and laying sample; walks from one pass end on the same minimum
    key = np.unique(inv.astype(np.int64).ravel() * t.size + laid_by)
    cell, start = key // t.size, key % t.size
    sel = inside[cell]
    cell, start = cell[sel], start[sel]
    t_cpa, slant, j = descend_to_pass(track.enu, t, pts[cell], start)
    order = np.lexsort((j, cell))
    cell, j, t_cpa, slant = cell[order], j[order], t_cpa[order], slant[order]
    same = np.zeros(cell.size, dtype=bool)
    same[1:] = (cell[1:] == cell[:-1]) & (j[1:] - j[:-1] <= 2)
    v_cpa = np.interp(t_cpa, fl.time_unix, fl.gs_mps)
    band = event.at_speed(v_cpa)["d_m"]
    ok = ~same & (slant >= band[0]) & (slant <= np.minimum(band[1], cfg.max_distance_m))
    ok &= (v_cpa >= event.v_mps.lo) & (v_cpa <= event.v_mps.hi)
    heard = t_cpa + slant / cfg.speed_of_sound
    c = cell[ok]
    return TubeCells(flight_idx, ids[c], clat[c], clon[c], heard[ok], slant[ok], cg[c], v_cpa[ok])


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
    v1: float = float("nan")
    """Ground speed of flight 1 at the sample that met, m/s; NaN when unknown."""
    v2: float = float("nan")


@dataclass
class PairHints:
    """Pair hints as arrays: flight indices and meeting places, one row per hint."""

    f1: IntArray
    f2: IntArray
    xy: FloatArray
    """(n, 2) meeting places in the flight set's tangent plane."""
    speed: FloatArray = field(default_factory=lambda: np.zeros((0, 2)))
    """(n, 2) ground speeds of the two flights at the samples that met, m/s."""
    raw_hits: int = 0
    """Sample pairs the ball queries returned before deduplication."""

    def __len__(self) -> int:
        return int(self.f1.shape[0])

    def __getitem__(self, i: int) -> PairHint:
        if self.speed.shape[0] == len(self):
            v1, v2 = float(self.speed[i, 0]), float(self.speed[i, 1])
        else:
            v1 = v2 = float("nan")
        return PairHint(int(self.f1[i]), int(self.f2[i]), self.xy[i], v1, v2)

    def __iter__(self):  # noqa: ANN204
        for i in range(len(self)):
            yield self[i]

    @staticmethod
    def empty() -> PairHints:
        """No hints."""
        return PairHints(np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 2)))


def sample_starts(
    fs: FlightSet, idx: IntArray, event: FlyoverEvent, cfg: FunnelConfig
) -> tuple[FloatArray, float]:
    """Recording start each indexed sample implies for an event, and the largest uncertainty.

    Were the closest approach to the site at the sample, the event was heard at the sample's
    time plus the slant distance over c, and the recording began the event's heard t0 before
    that; distance and t0 are the event's own at the sample's speed (`FlyoverEvent.at_speed`),
    so a profiled event lends each sample the narrow band its speed allows. The uncertainty per
    sample is half the distance band over c plus half the t0 band; the largest is returned.
    """
    band = event.at_speed(fs.sample_speed[idx])
    d_lo, d_hi = band["d_m"][0], np.minimum(band["d_m"][1], cfg.max_distance_m)
    t_lo, t_hi = band["t0_s"]
    c = cfg.speed_of_sound
    start = fs.sample_t[idx] + 0.5 * (d_lo + d_hi) / c - 0.5 * (t_lo + t_hi)
    half = 0.5 * np.maximum(d_hi - d_lo, 0.0) / c + 0.5 * (t_hi - t_lo)
    return start, float(half.max()) if half.size else 0.0


def pair_prefilter(
    fs: FlightSet, e1: FlyoverEvent, e2: FlyoverEvent, gap: Range, cfg: FunnelConfig
) -> PairHints:
    """Flight pairs that could produce two flyovers `gap` apart, and where they meet.

    In 'start' mode (the default) each event's samples are indexed in (x, y, scaled implied
    recording start, `sample_starts`), so a ball query around each event-2 sample finds event-1
    samples close in space whose implied starts agree within the events' widths, the timing
    tolerance and one index step (each sample lies within half a step of its flight's closest
    approach); the reach grows by the half step either flight can fly. In 'gap' mode event-1
    samples are indexed by time shifted by the gap, with a slack that must cover a sample
    anywhere within the reach of closest approach, reach over the slowest speed. One hint is
    kept per flight pair per meeting place (coarse cells twice the reach wide): two aircraft
    working the same airport meet again
    and again, and every meeting place has to be searched, not just the first one found. The
    queries run in chunks and the hits are reduced with array operations, since a continental
    day returns hundreds of millions of sample pairs. The list stops at `cfg.max_pairs` hints,
    in index order, and the caller reports that.
    """
    m1 = speed_mask(fs, e1)
    m2 = speed_mask(fs, e2)
    if not m1.any() or not m2.any():
        return PairHints.empty()
    reach = pair_reach(e1, e2, cfg)
    coarse = 2.0 * reach
    i1 = np.flatnonzero(m1)
    i2 = np.flatnonzero(m2)
    if cfg.prefilter == "gap":
        v_min = max(min(e1.v_mps.lo, e2.v_mps.lo), cfg.min_speed_mps)
        slack = cfg.timing_tolerance_s + gap.half_width + reach / v_min + cfg.index_step_s
        scale = reach / slack
        t1 = fs.sample_t[i1] + gap.mid
        t2 = fs.sample_t[i2]
    elif cfg.prefilter == "start":
        t1, h1 = sample_starts(fs, i1, e1, cfg)
        t2, h2 = sample_starts(fs, i2, e2, cfg)
        v_top = float(fs.sample_speed[i1].max()) + float(fs.sample_speed[i2].max())
        reach += 0.5 * cfg.index_step_s * v_top
        slack = cfg.timing_tolerance_s + cfg.index_step_s + h1 + h2
        scale = reach / slack
    else:
        raise ValueError(f"unknown prefilter mode {cfg.prefilter!r}")
    pts1 = np.column_stack([fs.sample_xy[i1], t1 * scale])
    pts2 = np.column_stack([fs.sample_xy[i2], t2 * scale])
    tree = cKDTree(pts1)
    keys: list[IntArray] = []
    mids: list[FloatArray] = []
    speeds: list[FloatArray] = []
    raw = 0
    for lo in range(0, pts2.shape[0], PREFILTER_CHUNK):
        hits = tree.query_ball_point(pts2[lo : lo + PREFILTER_CHUNK], reach * np.sqrt(2.0))
        counts = np.fromiter((len(h) for h in hits), dtype=np.int64, count=len(hits))
        total = int(counts.sum())
        raw += total
        if total == 0:
            continue
        q = np.repeat(np.arange(lo, lo + len(hits)), counts)
        h = np.fromiter(itertools.chain.from_iterable(hits), dtype=np.int64, count=total)
        f1 = fs.sample_flight[i1[h]]
        f2 = fs.sample_flight[i2[q]]
        keep = f1 != f2
        if not keep.any():
            continue
        q, h, f1, f2 = q[keep], h[keep], f1[keep], f2[keep]
        mid = 0.5 * (fs.sample_xy[i1[h]] + fs.sample_xy[i2[q]])
        key = np.column_stack([f1, f2, np.floor(mid / coarse).astype(np.int64)])
        _, first = np.unique(key, axis=0, return_index=True)
        first.sort()
        keys.append(key[first])
        mids.append(mid[first])
        speeds.append(np.column_stack([fs.sample_speed[i1[h]], fs.sample_speed[i2[q]]])[first])
    if not keys:
        return PairHints(
            np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 2)), raw_hits=raw
        )
    key = np.concatenate(keys)
    mid = np.concatenate(mids)
    speed = np.concatenate(speeds)
    _, first = np.unique(key, axis=0, return_index=True)
    first.sort()
    first = first[: cfg.max_pairs]
    return PairHints(
        key[first, 0].copy(), key[first, 1].copy(), mid[first].copy(), speed[first].copy(), raw
    )


def pair_reach(e1: FlyoverEvent, e2: FlyoverEvent, cfg: FunnelConfig) -> float:
    """How far apart two flights can be while both pass within range of one point."""
    return 2.0 * min(max(e1.d_m.hi, e2.d_m.hi), cfg.max_distance_m) + cfg.grid_spacing_m


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
    """Cells in both tubes whose heard closest-approach times differ by the gap.

    The two-tube join `pair_cells` replaced, kept as its reference: every pass of a cell in one
    tube is paired with every pass of the same cell in the other.
    """
    o2 = np.argsort(t2.ids, kind="stable")
    ids2 = t2.ids[o2]
    lo = np.searchsorted(ids2, t1.ids, side="left")
    counts = np.searchsorted(ids2, t1.ids, side="right") - lo
    total = int(counts.sum())
    if total == 0:
        return Candidates.empty()
    i1 = np.repeat(np.arange(t1.ids.size), counts)
    within = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
    i2 = o2[np.repeat(lo, counts) + within]
    diff = t2.heard[i2] - t1.heard[i1]
    ok = np.abs(diff - gap.mid) <= gap.half_width + cfg.timing_tolerance_s
    if not ok.any():
        return Candidates.empty()
    i1, i2 = i1[ok], i2[ok]
    n = i1.size
    return Candidates(
        cell=t1.ids[i1],
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


def band_width(event: FlyoverEvent, v: float, cap: float) -> float:
    """Width of the event's distance band at speed `v`, its whole range when `v` is unknown."""
    if not np.isfinite(v):
        return min(event.d_m.hi, cap) - event.d_m.lo
    lo, hi = event.at_speed(v)["d_m"]
    return float(min(hi[0], cap) - lo[0])


def pair_cells(
    fs: FlightSet,
    hint: PairHint,
    e1: FlyoverEvent,
    e2: FlyoverEvent,
    lattice: Lattice,
    cfg: FunnelConfig,
    ground_height: GroundHeight | None,
    radius: float,
) -> Candidates:
    """Cells near a hint in both tubes where the two events imply the same recording start.

    Only the narrower tube, the event whose distance band is narrower at the speed its flight
    had where the two met, is laid on the lattice, each cell on the pass that laid it. Each
    event implies a recording start, its heard closest approach minus its closest-approach time
    at that speed, and the two must agree within their widths and the timing tolerance; so each
    cell's start says when the other flight must have been heard, and its closest approach is
    looked for on the pass heard then (`heard_pass`), not at its nearest sample, which for
    pattern traffic is often another circuit. Its slant distance and speed must lie inside its
    event's ranges at that speed, then the starts must agree. The wide tube of a distant, fast
    aircraft is never laid; laying it was the funnel's memory and time at continental scale.
    """
    d1 = min(e1.d_m.hi, cfg.max_distance_m)
    d2 = min(e2.d_m.hi, cfg.max_distance_m)
    first_narrow = band_width(e1, hint.v1, d1) <= band_width(e2, hint.v2, d2)
    f_n, e_n, f_w, e_w = (hint.f1, e1, hint.f2, e2) if first_narrow else (hint.f2, e2, hint.f1, e1)
    d_w = d2 if first_narrow else d1
    tube = tube_cells(fs, f_n, e_n, lattice, cfg, ground_height, hint.xy, radius)
    if tube.ids.size == 0:
        return Candidates.empty()
    c = cfg.speed_of_sound
    t0_n = e_n.at_speed(tube.speed)["t0_s"]
    start_n = tube.heard - 0.5 * (t0_n[0] + t0_n[1])
    half_n = 0.5 * (t0_n[1] - t0_n[0])
    # when the other event can be heard, over every speed (at_speed stays inside t0_s), with a
    # second to spare for the refined closest approach
    spare = half_n + cfg.timing_tolerance_s + 1.0
    fl_w = fs.flights[f_w]
    track_w = fs.track(f_w)
    pts = geodesy.geodetic_to_ecef(tube.lat, tube.lon, tube.height)
    t_cpa, slant, found = heard_pass(
        track_w.enu,
        track_w.t,
        pts,
        start_n + e_w.t0_s.lo - spare,
        start_n + e_w.t0_s.hi + spare,
        c,
        d_w,
    )
    speed = np.interp(t_cpa, fl_w.time_unix, fl_w.gs_mps)
    band = e_w.at_speed(speed)["d_m"]
    ok = found & (slant >= band[0]) & (slant <= np.minimum(band[1], d_w))
    ok &= (speed >= e_w.v_mps.lo) & (speed <= e_w.v_mps.hi)
    heard_w = t_cpa + slant / c
    t0_w = e_w.at_speed(speed)["t0_s"]
    start_w = heard_w - 0.5 * (t0_w[0] + t0_w[1])
    slack = half_n + 0.5 * (t0_w[1] - t0_w[0]) + cfg.timing_tolerance_s
    ok &= np.abs(start_w - start_n) <= slack
    if not ok.any():
        return Candidates.empty()
    heard1, heard2 = (tube.heard, heard_w) if first_narrow else (heard_w, tube.heard)
    slant1, slant2 = (tube.slant, slant) if first_narrow else (slant, tube.slant)
    start1 = start_n if first_narrow else start_w
    n = int(ok.sum())
    return Candidates(
        cell=tube.ids[ok],
        lat=tube.lat[ok],
        lon=tube.lon[ok],
        f1=np.full(n, hint.f1, dtype=np.int64),
        f2=np.full(n, hint.f2, dtype=np.int64),
        heard1=heard1[ok],
        heard2=heard2[ok],
        slant1=slant1[ok],
        slant2=slant2[ok],
        t_start=start1[ok],
    )


def spatial_order(xy: FloatArray, block_m: float = 50000.0) -> IntArray:
    """Row-major order of points over blocks `block_m` wide, so neighbors come one after another."""
    xy = np.atleast_2d(np.asarray(xy, dtype=float))
    if xy.shape[0] == 0:
        return np.zeros(0, np.int64)
    bx = np.floor(xy[:, 0] / block_m)
    by = np.floor(xy[:, 1] / block_m)
    return np.lexsort((xy[:, 0], bx, by)).astype(np.int64)


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
    """Speed gate, pair prefilter, narrow tube and exact check. Returns (candidates, hints).

    Each hint's work is restricted to the track segments near its meeting place (three times
    the reach, which covers the far corner of a hint's coarse cell), so one pair of aircraft that
    meet in several places costs one pass per place rather than a region-wide tube. Hints run in
    spatial order so that consecutive hints share terrain tiles and cached tracks.
    """
    hints = pair_prefilter(fs, e1, e2, gap, cfg)
    if progress is not None:
        progress(f"prefilter: {hints.raw_hits} sample pairs, {len(hints)} pair hints")
    radius = 3.0 * pair_reach(e1, e2, cfg)
    parts: list[Candidates] = []
    order = spatial_order(hints.xy)
    for k, j in enumerate(order.tolist(), start=1):
        joined = pair_cells(fs, hints[j], e1, e2, lattice, cfg, ground_height, radius)
        if len(joined):
            parts.append(joined)
        if progress is not None and k % 500 == 0:
            progress(
                f"funnel: {k}/{len(hints)} pair hints, {sum(len(p) for p in parts)} candidates"
            )
    found = Candidates.concat(parts)
    cand = unique_hypotheses(found)
    if progress is not None and len(cand) < len(found):
        progress(
            f"funnel: {len(found)} rows, {len(cand)} after dropping rows that overlapping hints "
            "found twice"
        )
    return cand, len(hints)


def unique_hypotheses(cand: Candidates, start_tol_s: float = 1.0) -> Candidates:
    """One row per cell, flight pair and recording start (to `start_tol_s`), in found order.

    A hint searches a disc three reaches wide around its meeting place, wider than the coarse
    cells that tell meeting places apart, so the hints of one pair overlap and find the same cells
    again: about half the rows on real days, each of which was scored and silence-checked anew.
    Rows of one cell and pair with different starts (pattern traffic heard on another circuit)
    are different hypotheses and stay.
    """
    if len(cand) < 2:
        return cand
    order = np.lexsort((cand.t_start, cand.f2, cand.f1, cand.cell))
    c, a, b, t = cand.cell[order], cand.f1[order], cand.f2[order], cand.t_start[order]
    same = np.zeros(order.size, dtype=bool)
    same[1:] = (c[1:] == c[:-1]) & (a[1:] == a[:-1]) & (b[1:] == b[:-1])
    same[1:] &= t[1:] - t[:-1] < start_tol_s
    return cand.subset(np.sort(order[~same]))


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
        t0 = event.at_speed(tube.speed)["t0_s"]
        ok = np.abs(tube.heard - (t_known + 0.5 * (t0[0] + t0[1]))) <= (
            0.5 * (t0[1] - t0[0]) + tolerance_s
        )
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
    fs: FlightSet,
    cand: Candidates,
    windows: list[SilenceWindow],
    cfg: FunnelConfig,
    cand_height: FloatArray | None = None,
) -> IntArray:
    """Distinct flights within the silence distance of each candidate during its silent windows.

    Distance is the slant range from the candidate (at `cand_height` above the ellipsoid, zero
    by default) to each indexed sample, and a sample counts at the time its sound arrives,
    emission time plus travel time, because silence windows are marked in recording time. A
    candidate's own event flights are not counted: the user marked when they were heard, and a
    few seconds of the same aircraft lingering at the edge of that window is not a contradiction.
    """
    if not windows or len(cand) == 0 or len(fs) == 0:
        return np.zeros(len(cand), dtype=np.int64)
    xy = fs.xy(cand.lat, cand.lon)
    heights = np.zeros(len(cand)) if cand_height is None else np.asarray(cand_height, dtype=float)
    tree = fs.tree()
    out = np.zeros(len(cand), dtype=np.int64)
    # the hit lists of every candidate at once ran to tens of gigabytes beside busy airports
    for lo in range(0, len(cand), SILENCE_CHUNK):
        lists = tree.query_ball_point(xy[lo : lo + SILENCE_CHUNK], cfg.silence_distance_m)
        for k, hits in enumerate(lists):
            if hits:
                out[lo + k] = _silence_count(fs, cand, lo + k, hits, xy, heights, windows, cfg)
    return out


def _silence_count(
    fs: FlightSet,
    cand: Candidates,
    i: int,
    hits: list[int],
    xy: FloatArray,
    heights: FloatArray,
    windows: list[SilenceWindow],
    cfg: FunnelConfig,
) -> int:
    """Other flights heard by candidate i inside a silence window, from its nearby samples."""
    idx = np.asarray(hits, dtype=np.int64)
    horizontal = np.hypot(fs.sample_xy[idx, 0] - xy[i, 0], fs.sample_xy[idx, 1] - xy[i, 1])
    slant = np.hypot(horizontal, fs.sample_h[idx] - heights[i])
    rel = fs.sample_t[idx] + slant / cfg.speed_of_sound - cand.t_start[i]
    inside = slant <= cfg.silence_distance_m
    in_window = np.zeros(idx.size, dtype=bool)
    for w in windows:
        in_window |= (rel >= w.start_s) & (rel <= w.end_s)
    inside &= in_window
    if not inside.any():
        return 0
    others = np.unique(fs.sample_flight[idx[inside]])
    own = {int(cand.f1[i]), int(cand.f2[i])}
    return sum(1 for f in others if int(f) not in own)
