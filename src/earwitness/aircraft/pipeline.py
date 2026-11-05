"""End-to-end aircraft analysis: recording, measured events and a day of flights to a run folder.

Stages: whitened spectrogram, flight set over the region, candidate funnel (two-event pair mode
or single-event known-time mode), silence filter (hard cut, or a score penalty in soft mode),
exact-curve scoring of every candidate, grouping by flight pair, two-stage refinement (100 m,
then 25 m) of the best pairs, and outputs (results.parquet, heatmap.geojson, overlays with
modeled curves, summary, run log).
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray

from earwitness import __version__
from earwitness.aircraft.funnel import (
    Candidates,
    FlightSet,
    FunnelConfig,
    GroundHeight,
    Lattice,
    silence_hits,
    single_event_candidates,
    two_event_candidates,
)
from earwitness.aircraft.prep import Flight
from earwitness.aircraft.score import (
    CandidateScore,
    ScoreConfig,
    SpectrogramIndex,
    modeled_curve,
    score_candidate,
)
from earwitness.audio.decode import read_audio, resample
from earwitness.audio.events import VALLEY_HALF_T0_S, EventSet
from earwitness.audio.spectrogram import Spectrogram, ridge, spectrogram
from earwitness.core import geodesy
from earwitness.core.grid import BBox, local_grid
from earwitness.project import RunLog, sha256_file, toml_dumps, utc_iso

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


@dataclass(frozen=True)
class AircraftPriors:
    """Time range the recording may have started in, and the region to search."""

    start: datetime
    end: datetime
    region: BBox

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("priors need timezone-aware UTC datetimes")
        if self.end <= self.start:
            raise ValueError("prior end must be after start")


@dataclass(frozen=True)
class AircraftPipelineConfig:
    """Everything tunable in one place; written to the run folder as config.toml."""

    funnel: FunnelConfig = field(default_factory=FunnelConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)
    spectrogram_rate_hz: int = 4000
    nfft: int = 2048
    hop: int = 512
    fmax_hz: float = 600.0
    top_pairs: int = 10
    refine_spacing_m: float = 100.0
    refine_half_extent_m: float = 300.0
    refine_fine_spacing_m: float = 25.0
    refine_fine_half_extent_m: float = 100.0
    heatmap_pairs: int = 3
    heatmap_half_extent_m: float = 1500.0
    max_candidates: int = 30000
    use_terrain: bool = True
    workers: int = 1
    """Processes that score candidates; 0 means one per CPU. Scoring is independent per
    candidate and was most of a large search's time on one core."""
    parallel_min_rows: int = 2000
    """Fewer candidates than this are scored in the calling process."""
    parallel_chunk_rows: int = 1000
    """Candidates per task sent to a worker; each task carries only the flights it needs."""


_WORKER: dict[str, object] = {}
"""Per-process scoring state set by `_init_scoring_worker`."""


def _init_scoring_worker(
    spec: Spectrogram,
    events: EventSet,
    score_cfg: ScoreConfig,
    known_time_tolerance_s: float | None,
) -> None:
    _WORKER.update(
        index=SpectrogramIndex(spec),
        events=events,
        cfg=score_cfg,
        known=known_time_tolerance_s,
    )


def _score_chunk(
    flights: dict[int, Flight], f1: IntArray, f2: IntArray, xyz: FloatArray, t_start: FloatArray
) -> list[CandidateScore]:
    """Score candidate rows in a worker; `flights` holds every flight the rows refer to."""
    index: SpectrogramIndex = _WORKER["index"]  # type: ignore[assignment]
    events: EventSet = _WORKER["events"]  # type: ignore[assignment]
    cfg: ScoreConfig = _WORKER["cfg"]  # type: ignore[assignment]
    tracks = {k: fl.track_ecef(1.0) for k, fl in flights.items()}
    out: list[CandidateScore] = []
    for i in range(xyz.shape[0]):
        rows = [tracks[int(f1[i])]]
        if not events.single_event:
            rows.append(tracks[int(f2[i])])
        out.append(
            score_candidate(
                index, rows, xyz[i], events, float(t_start[i]), cfg,
                known_time_tolerance_s=_WORKER["known"],  # type: ignore[arg-type]
            )
        )  # fmt: skip
    return out


def _worker_count(cfg: AircraftPipelineConfig) -> int:
    return (os.cpu_count() or 1) if cfg.workers <= 0 else cfg.workers


def _score_rows(
    index: SpectrogramIndex,
    fs: FlightSet,
    cand: Candidates,
    lat: FloatArray,
    lon: FloatArray,
    height: FloatArray,
    events: EventSet,
    cfg: AircraftPipelineConfig,
    known_time_tolerance_s: float | None,
    spec: Spectrogram | None = None,
) -> list[CandidateScore]:
    xyz = geodesy.geodetic_to_ecef(lat, lon, height)
    workers = _worker_count(cfg)
    if workers > 1 and lat.size >= cfg.parallel_min_rows and spec is not None:
        return _score_rows_parallel(
            spec, fs, cand, xyz, events, cfg, known_time_tolerance_s, workers
        )
    out: list[CandidateScore] = []
    for i in range(lat.size):
        tracks = [fs.track(int(cand.f1[i]))]
        if not events.single_event:
            tracks.append(fs.track(int(cand.f2[i])))
        out.append(
            score_candidate(
                index,
                tracks,
                xyz[i],
                events,
                float(cand.t_start[i]),
                cfg.score,
                known_time_tolerance_s=known_time_tolerance_s,
            )
        )
    return out


def _score_rows_parallel(
    spec: Spectrogram,
    fs: FlightSet,
    cand: Candidates,
    xyz: FloatArray,
    events: EventSet,
    cfg: AircraftPipelineConfig,
    known_time_tolerance_s: float | None,
    workers: int,
) -> list[CandidateScore]:
    """`_score_rows` over a process pool: chunks of rows in order, each with its own flights.

    Candidates arrive grouped by flight pair, so a chunk needs few flights; each worker builds
    the 1 s tracks of the flights in its chunk. Scores are the serial ones exactly, in order.
    """
    n = xyz.shape[0]
    step = max(1, cfg.parallel_chunk_rows)
    tasks = []
    for lo in range(0, n, step):
        sl = slice(lo, min(lo + step, n))
        f1 = cand.f1[sl].astype(np.int64)
        f2 = cand.f2[sl].astype(np.int64)
        used = set(f1.tolist()) | ({int(k) for k in f2.tolist() if k >= 0})
        tasks.append(({k: fs.flights[k] for k in used}, f1, f2, xyz[sl], cand.t_start[sl]))
    out: list[CandidateScore] = []
    with ProcessPoolExecutor(
        max_workers=min(workers, len(tasks)),
        initializer=_init_scoring_worker,
        # the workers look up whitened brightness only; the power array stays here
        initargs=(
            Spectrogram(spec.t, spec.f, np.zeros((0, 0)), spec.whitened_db),
            events,
            cfg.score,
            known_time_tolerance_s,
        ),
    ) as pool:
        for part in pool.map(_score_chunk, *zip(*tasks, strict=True)):
            out.extend(part)
    return out


def _refine_around(
    index: SpectrogramIndex,
    fs: FlightSet,
    cand: Candidates,
    i: int,
    lat0: float,
    lon0: float,
    half_extent_m: float,
    spacing_m: float,
    ground: GroundHeight,
    events: EventSet,
    cfg: AircraftPipelineConfig,
    known_time_tolerance_s: float | None,
    penalty_db: float,
) -> tuple[FloatArray, FloatArray, list[CandidateScore], FloatArray]:
    """Score candidate `i`'s flights on a local grid around a point.

    Returns (lat, lon, scores, totals with the silence penalty applied).
    """
    rlat, rlon = local_grid(lat0, lon0, half_extent_m, spacing_m)
    sub = Candidates(
        cell=np.full(rlat.size, cand.cell[i]), lat=rlat, lon=rlon,
        f1=np.full(rlat.size, cand.f1[i]), f2=np.full(rlat.size, cand.f2[i]),
        heard1=np.full(rlat.size, cand.heard1[i]), heard2=np.full(rlat.size, cand.heard2[i]),
        slant1=np.full(rlat.size, cand.slant1[i]), slant2=np.full(rlat.size, cand.slant2[i]),
        t_start=np.full(rlat.size, cand.t_start[i]),
    )  # fmt: skip
    rs = _score_rows(
        index, fs, sub, rlat, rlon, ground(rlat, rlon), events, cfg, known_time_tolerance_s
    )
    return rlat, rlon, rs, np.array([s.total for s in rs]) - penalty_db


KNOWN_TIME_PAD_S = 120.0
"""Seconds beyond a known clip window that flights are still admitted for: sound from 10 km
away arrives 30 s late, and the scorer's window reaches a minute either side of an event."""


def flight_window(
    priors: AircraftPriors, events: EventSet, duration_s: float
) -> tuple[float, float]:
    """Unix-time window a flight must touch to matter: the date prior widened by the clip, or,
    with a known start, the clip itself give or take the clock tolerance and a margin.

    A known time bounds every flyover the recording can hold, so flights airborne only outside
    the clip cannot be a source or break a silence window; dropping them before the funnel keeps
    every candidate the clock would keep afterwards, at a fraction of the work. The window is
    empty (start after end) when the known time lies outside the date prior.
    """
    lo = priors.start.timestamp() - duration_s
    hi = priors.end.timestamp() + duration_s
    if events.known_time is not None:
        t_known = events.known_time.timestamp()
        pad = events.known_time_tolerance_s + KNOWN_TIME_PAD_S
        lo = max(lo, t_known - pad)
        hi = min(hi, t_known + duration_s + pad)
    return lo, hi


def _flight_label(fs: FlightSet, idx: int) -> dict[str, object]:
    if idx < 0:
        return {}
    fl = fs.flights[idx]
    return {
        "icao": fl.icao,
        "leg": fl.leg,
        "type_code": fl.type_code,
        "registration": fl.registration,
        "engine_class": fl.engine_class,
    }


def analyze_aircraft(
    recording: str | Path,
    events: EventSet,
    flights: list[Flight],
    priors: AircraftPriors,
    out_dir: str | Path,
    cfg: AircraftPipelineConfig | None = None,
    terrain: object | None = None,
    progress: object | None = None,
) -> dict[str, object]:
    """Run the aircraft pipeline and write the run folder. Returns the summary dict."""
    cfg = cfg or AircraftPipelineConfig()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log = RunLog(out / "run.log", echo=progress)  # type: ignore[arg-type]
    t_wall = time.perf_counter()
    rec = Path(recording)
    log.log(f"earwitness {__version__} aircraft analysis of {rec.name}")

    samples, rate = read_audio(rec)
    duration = samples.size / rate
    if rate != cfg.spectrogram_rate_hz:
        samples = resample(samples, rate, cfg.spectrogram_rate_hz)
        rate = cfg.spectrogram_rate_hz
    spec = spectrogram(samples, rate, nfft=cfg.nfft, hop=cfg.hop, fmax=cfg.fmax_hz)
    index = SpectrogramIndex(spec)
    log.log(
        f"recording: {duration:.1f} s; spectrogram {spec.f.size} bins x {spec.t.size} frames "
        f"({index.df:.2f} Hz, {index.dt:.3f} s)"
    )

    mode = "single-event known-time" if events.single_event else "two-event pair"
    if events.single_event and events.known_time is None:
        raise ValueError("single-event mode needs known_time_utc in the events file")
    t_lo, t_hi = flight_window(priors, events, duration)
    if t_hi < t_lo:
        log.log("the known time lies outside the date prior: no flight can match")
        fs = FlightSet.build([], priors.region, cfg.funnel)
    else:
        fs = FlightSet.build(flights, priors.region, cfg.funnel, t_lo, t_hi)
    clock = " within the known clip window" if events.known_time is not None else ""
    log.log(
        f"{mode}: {len(fs)} of {len(flights)} flights admitted{clock}; "
        f"{fs.sample_t.size} indexed samples"
    )
    ground = GroundHeight(terrain if cfg.use_terrain else None)
    lattice = Lattice(priors.region, cfg.funnel.grid_spacing_m)

    t0 = time.perf_counter()
    if events.single_event:
        t_known = events.known_time.timestamp()  # type: ignore[union-attr]
        cand = single_event_candidates(
            fs,
            events.events[0],
            t_known,
            events.known_time_tolerance_s,
            lattice,
            cfg.funnel,
            ground,
        )
        n_pairs = 0
    else:
        cand, n_pairs = two_event_candidates(
            fs,
            events.events[0],
            events.events[1],
            events.gap(),
            lattice,
            cfg.funnel,
            ground,
            log.log,
        )
    capped = not events.single_event and n_pairs >= cfg.funnel.max_pairs
    log.log(
        f"funnel: {n_pairs} flight pairs, {len(cand)} candidate cells "
        f"in {time.perf_counter() - t0:.1f} s"
        + (" (pair cap reached; raise max_pairs or narrow the priors)" if capped else "")
    )
    if not events.single_event and events.known_time is not None and len(cand):
        # the clock as a second prior on a two-event search: a candidate's implied start must
        # sit within the clock tolerance, the timing tolerance and event 1's own width
        e1 = events.events[0]
        half = VALLEY_HALF_T0_S if e1.profiled else e1.t0_s.half_width
        slack = events.known_time_tolerance_s + cfg.funnel.timing_tolerance_s + half
        keep = np.abs(cand.t_start - events.known_time.timestamp()) <= slack
        cand = cand.subset(keep)
        log.log(
            f"known time with two events: {len(cand)} of {keep.size} candidates start within "
            f"{slack:.1f} s of the clock"
        )
    counts: dict[str, int] = {"flights": len(fs), "pairs": n_pairs, "candidates": len(cand)}

    heights = ground(cand.lat, cand.lon)
    if cfg.funnel.silence_mode != "off" and events.silence and len(cand):
        hits = silence_hits(fs, cand, events.silence, cfg.funnel, heights)
        cand.silence_hits = hits
        if cfg.funnel.silence_mode == "hard":
            keep = hits == 0
            cand = cand.subset(keep)
            heights = heights[keep]
        log.log(
            f"silence filter ({cfg.funnel.silence_mode}): {int((hits > 0).sum())} with hits, "
            f"{len(cand)} kept"
        )
    counts["after_silence"] = len(cand)
    if len(cand) > cfg.max_candidates:
        rng = np.random.default_rng(0)
        pick = np.sort(rng.choice(len(cand), cfg.max_candidates, replace=False))
        cand = cand.subset(pick)
        heights = heights[pick]
        log.log(f"capped to {cfg.max_candidates} candidates")
    # soft mode keeps candidates with silence hits but ranks them lower
    penalty = np.zeros(len(cand))
    if cfg.funnel.silence_mode == "soft" and cand.silence_hits.size == len(cand):
        penalty = cfg.funnel.silence_penalty_db * cand.silence_hits.astype(float)

    # known-time mode: the scorer sweeps the clock tolerance plus each candidate's own
    # closest-approach width (see `time_sweep_for`); two-event mode sweeps its default
    known_tol = events.known_time_tolerance_s if events.single_event else None
    t0 = time.perf_counter()
    scores = _score_rows(index, fs, cand, cand.lat, cand.lon, heights, events, cfg, known_tol, spec)
    total = np.array([s.total for s in scores]) - penalty if scores else np.zeros(0)
    workers = _worker_count(cfg) if len(cand) >= cfg.parallel_min_rows else 1
    log.log(
        f"scored {len(cand)} candidates in {time.perf_counter() - t0:.1f} s"
        + (f" on {workers} processes" if workers > 1 else "")
    )

    # group by flight pair: best cell per pair
    pair_key = cand.f1 * 1_000_000 + np.where(cand.f2 >= 0, cand.f2, 0)
    best_per_pair: dict[int, int] = {}
    for i in np.argsort(-total, kind="stable"):
        k = int(pair_key[i])
        if k not in best_per_pair:
            best_per_pair[k] = int(i)
    ranked = sorted(best_per_pair.values(), key=lambda i: -total[i])[: cfg.top_pairs]

    # refinement around each top pair's best cell: 100 m within 300 m, then 25 m within 100 m
    # of that best point, since a 50 m error already costs about a decibel near closest approach
    t0 = time.perf_counter()
    finals: list[dict[str, object]] = []
    for rank, i in enumerate(ranked, start=1):
        rlat, rlon, rs, rt = _refine_around(
            index, fs, cand, int(i), float(cand.lat[i]), float(cand.lon[i]),
            cfg.refine_half_extent_m, cfg.refine_spacing_m, ground, events, cfg, known_tol,
            float(penalty[i]),
        )  # fmt: skip
        j = int(np.argmax(rt))
        flat, flon, fine, ft = _refine_around(
            index, fs, cand, int(i), float(rlat[j]), float(rlon[j]),
            cfg.refine_fine_half_extent_m, cfg.refine_fine_spacing_m, ground, events, cfg,
            known_tol, float(penalty[i]),
        )  # fmt: skip
        k = int(np.argmax(ft))
        if ft[k] >= rt[j]:
            rlat, rlon, rs, rt, j = flat, flon, fine, ft, k
        best = rs[j]
        finals.append(
            {
                "rank": rank,
                "lat": float(rlat[j]),
                "lon": float(rlon[j]),
                "score": float(rt[j]),
                "coarse_score": float(total[i]),
                "silence_penalty_db": float(penalty[i]),
                "event_scores": [float(e.score) for e in best.events],
                "f0_hz": [float(e.f0_hz) for e in best.events],
                "time_shift_s": float(best.events[0].dt_s),
                "t_start_unix": float(best.t_start),
                "t_start_utc": utc_iso(datetime.fromtimestamp(float(best.t_start), tz=UTC), True),
                "trace_rms_hz": None if np.isnan(best.trace_rms_hz) else float(best.trace_rms_hz),
                "silence_hits": int(cand.silence_hits[i]) if cand.silence_hits.size else 0,
                "flights": [_flight_label(fs, int(cand.f1[i])), _flight_label(fs, int(cand.f2[i]))],
                "cell_index": int(i),
            }
        )
    finals.sort(key=lambda d: -float(d["score"]))  # type: ignore[arg-type]
    for rank, d in enumerate(finals, start=1):
        d["rank"] = rank
    log.log(f"refined {len(finals)} pairs in {time.perf_counter() - t0:.1f} s")

    # outputs
    table = pa.table(
        {
            "lat": pa.array(cand.lat),
            "lon": pa.array(cand.lon),
            "score": pa.array(total),
            "f1": pa.array(cand.f1),
            "f2": pa.array(cand.f2),
            "icao1": pa.array([fs.flights[int(k)].icao for k in cand.f1], type=pa.string()),
            "icao2": pa.array(
                ["" if k < 0 else fs.flights[int(k)].icao for k in cand.f2], type=pa.string()
            ),
            "heard1_unix": pa.array(cand.heard1),
            "heard2_unix": pa.array(cand.heard2),
            "slant1_m": pa.array(cand.slant1),
            "slant2_m": pa.array(cand.slant2),
            "t_start_unix": pa.array([s.t_start for s in scores], type=pa.float64()),
            "silence_hits": pa.array(
                cand.silence_hits if cand.silence_hits.size else np.zeros(len(cand), np.int64)
            ),
        }
    )
    pq.write_table(table, out / "results.parquet")

    features: list[dict[str, object]] = []
    for d in finals[: cfg.heatmap_pairs]:
        i = int(d["cell_index"])  # type: ignore[arg-type]
        hlat, hlon = local_grid(
            float(d["lat"]), float(d["lon"]), cfg.heatmap_half_extent_m, cfg.refine_spacing_m
        )  # type: ignore[arg-type]
        sub = Candidates(
            cell=np.full(hlat.size, cand.cell[i]), lat=hlat, lon=hlon,
            f1=np.full(hlat.size, cand.f1[i]), f2=np.full(hlat.size, cand.f2[i]),
            heard1=np.full(hlat.size, cand.heard1[i]), heard2=np.full(hlat.size, cand.heard2[i]),
            slant1=np.full(hlat.size, cand.slant1[i]), slant2=np.full(hlat.size, cand.slant2[i]),
            t_start=np.full(hlat.size, cand.t_start[i]),
        )  # fmt: skip
        hs = _score_rows(index, fs, sub, hlat, hlon, ground(hlat, hlon), events, cfg, known_tol)
        for la, lo, s in zip(hlat, hlon, hs, strict=True):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(lo), float(la)]},
                    "properties": {
                        "stage": "heatmap",
                        "rank": d["rank"],
                        "score": round(float(s.total - penalty[i]), 3),
                    },
                }
            )
    for d in finals:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
                "properties": {
                    "stage": "final",
                    "rank": d["rank"],
                    "score": round(float(d["score"]), 3),
                },  # type: ignore[arg-type]
            }
        )
    (out / "heatmap.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features})
    )

    overlays = out / "overlays"
    overlays.mkdir(exist_ok=True)
    for d in finals[: cfg.heatmap_pairs]:
        i = int(d["cell_index"])  # type: ignore[arg-type]
        xyz = geodesy.geodetic_to_ecef(
            float(d["lat"]),
            float(d["lon"]),
            float(ground(np.array([d["lat"]]), np.array([d["lon"]]))[0]),
        )  # type: ignore[arg-type]
        curves = []
        for k, event in enumerate(events.events):
            fidx = int(cand.f1[i]) if k == 0 else int(cand.f2[i])
            t_rec, f = modeled_curve(
                fs.track(fidx),
                xyz,
                event,
                float(d["t_start_unix"]),
                float(d["f0_hz"][k]),
                cfg.score,
                index,
            )  # type: ignore[arg-type,index]
            band_lo = (
                float(d["f0_hz"][k])
                * cfg.score.speed_of_sound
                / (cfg.score.speed_of_sound + event.v_mps.hi)
                * 0.9
            )  # type: ignore[index]
            band_hi = (
                float(d["f0_hz"][k])
                * cfg.score.speed_of_sound
                / (cfg.score.speed_of_sound - event.v_mps.hi)
                * 1.1
            )  # type: ignore[index]
            rt, rf = ridge(spec, band_lo, min(band_hi, cfg.fmax_hz - 1.0))
            win = (rt >= event.t0_s.mid - cfg.score.window_s) & (
                rt <= event.t0_s.mid + cfg.score.window_s
            )
            curves.append(
                {
                    "event": event.id,
                    "t_s": t_rec.tolist(),
                    "modeled_hz": f.tolist(),
                    "ridge_t_s": rt[win].tolist(),
                    "ridge_hz": rf[win].tolist(),
                }
            )
        (overlays / f"rank_{d['rank']:02d}.json").write_text(
            json.dumps({"lat": d["lat"], "lon": d["lon"], "curves": curves})
        )

    runtime = time.perf_counter() - t_wall
    summary: dict[str, object] = {
        "schema": "earwitness.aircraft-run/1",
        "earwitness_version": __version__,
        "mode": mode,
        "recording": {"file": rec.name, "sha256": sha256_file(rec), "duration_s": duration},
        "priors": {
            "start_utc": utc_iso(priors.start),
            "end_utc": utc_iso(priors.end),
            "region": priors.region.as_tuple(),
        },
        "events": events.to_dict(),
        "counts": counts,
        "top": finals,
        "runtime_s": runtime,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    config = {
        "funnel": asdict(cfg.funnel),
        "score": asdict(cfg.score),
        "pipeline": {k: v for k, v in asdict(cfg).items() if k not in ("funnel", "score")},
    }
    (out / "config.toml").write_text(toml_dumps(config), encoding="utf-8")
    log.log(f"done in {runtime:.1f} s; best {finals[0] if finals else 'none'}")
    return summary


def distance_to(summary: dict[str, object], lat: float, lon: float) -> list[float]:
    """Distances (m) from each ranked result to a reference point, for scoring test cases."""
    return [
        float(geodesy.horizontal_distance(t["lat"], t["lon"], lat, lon))  # type: ignore[index]
        for t in summary["top"]  # type: ignore[union-attr]
    ]
