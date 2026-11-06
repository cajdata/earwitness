"""End-to-end thunder analysis: recording plus priors plus lightning events to a run folder.

Stages: observed envelope, candidate grid with land mask and strike-count prefilter, coarse
sliding-cosine search over every start-time offset in the prior range, refinement of the best
regions, and outputs (results.parquet, heatmap.geojson, histogram.json, overlays, run log).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray
from scipy.signal import find_peaks
from shapely.geometry.base import BaseGeometry

from earwitness import __version__
from earwitness.audio.decode import read_audio
from earwitness.audio.envelope import (
    Envelope,
    EnvelopeConfig,
    baseline_subtract,
    observed_envelope,
    resample_envelope,
    wind_mask,
)
from earwitness.core import geodesy
from earwitness.core.grid import BBox, grid_points, points_in_geometry
from earwitness.data.adapters import LightningEvents
from earwitness.data.landmask import LandMask
from earwitness.project import RunLog, sha256_file, toml_dumps, utc_iso
from earwitness.thunder.model import ThunderConfig
from earwitness.thunder.refine import RefineConfig, pick_regions, refine
from earwitness.thunder.search import (
    CandidateResults,
    Progress,
    SearchConfig,
    StrikeIndex,
    render_models,
    search,
    strike_count_prefilter,
)

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class ThunderPriors:
    """What the user knows: the clip started somewhere in [start, end), maybe in a region."""

    start: datetime
    end: datetime
    region: BBox | None = None
    region_geometry: BaseGeometry | None = None

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("priors need timezone-aware UTC datetimes")
        if self.end <= self.start:
            raise ValueError("prior end must be after start")


@dataclass(frozen=True)
class PipelineConfig:
    """Everything tunable in one place; written to the run folder as config.toml."""

    search: SearchConfig = field(default_factory=SearchConfig)
    model: ThunderConfig = field(default_factory=ThunderConfig)
    refine: RefineConfig = field(default_factory=RefineConfig)
    grid_spacing_m: float = 5000.0
    top_k: int = 10
    do_refine: bool = True
    use_land_mask: bool = True
    mask_wind: bool = False
    baseline_percentile: float = 10.0
    overlay_count: int = 5


def peak_count(values: FloatArray, step_s: float) -> int:
    """Number of distinct loudness peaks, the basis of the prefilter default."""
    v = np.asarray(values, dtype=float)
    if v.size < 3 or v.max() <= 0:
        return 0
    peaks, _ = find_peaks(v, prominence=0.2 * v.max(), distance=max(1, round(10.0 / step_s)))
    return int(peaks.size)


def prepare_observed(
    samples: FloatArray, rate: int, cfg: PipelineConfig
) -> tuple[Envelope, Envelope, dict[str, object]]:
    """Observed envelope at the coarse step and at the refinement step, plus diagnostics."""
    env_cfg = EnvelopeConfig(smoothing_sigma_s=cfg.model.smoothing_sigma_s)
    env = observed_envelope(samples, rate, env_cfg)
    values = baseline_subtract(env.values, cfg.baseline_percentile)
    info: dict[str, object] = {"wind_fraction": 0.0}
    wind = wind_mask(samples, rate, env_cfg)
    info["wind_fraction"] = float(np.mean(wind)) if wind.size else 0.0
    if cfg.mask_wind and wind.size == values.size:
        values = np.where(wind, 0.0, values)
    cleaned = Envelope(t=env.t, values=values, frame_s=env.frame_s)
    coarse = resample_envelope(cleaned, cfg.search.step_s)
    fine = resample_envelope(cleaned, cfg.refine.step_s)
    info["peak_count"] = peak_count(coarse.values, cfg.search.step_s)
    info["duration_s"] = float(samples.size / rate)
    return coarse, fine, info


def candidate_grid(
    events: LightningEvents,
    priors: ThunderPriors,
    cfg: PipelineConfig,
    landmask: LandMask | None,
    log: Progress = None,
) -> tuple[FloatArray, FloatArray, BBox, dict[str, int]]:
    """Grid points over the prior region (or the strikes' extent), on land when masked."""
    region = priors.region or events.bbox().expand(cfg.search.r_max_m)
    lat, lon = grid_points(region, cfg.grid_spacing_m)
    counts = {"grid": int(lat.size)}
    if priors.region_geometry is not None and lat.size:
        keep = points_in_geometry(lat, lon, priors.region_geometry)
        lat, lon = lat[keep], lon[keep]
    counts["in_region"] = int(lat.size)
    if landmask is not None and lat.size:
        keep = landmask.contains(lat, lon)
        lat, lon = lat[keep], lon[keep]
    counts["on_land"] = int(lat.size)
    if log:
        log(f"grid: {counts['grid']} points at {cfg.grid_spacing_m / 1000:g} km, {lat.size} kept")
    return lat, lon, region, counts


def _histogram(scores: FloatArray, bins: int = 50) -> dict[str, list[float]]:
    counts, edges = np.histogram(scores, bins=bins, range=(-0.2, 1.0))
    return {"edges": edges.tolist(), "counts": counts.tolist()}


def _results_table(stages: list[CandidateResults]) -> pa.Table:
    rows = {
        "stage": [],
        "lat": [],
        "lon": [],
        "score": [],
        "offset_unix": [],
        "offset_utc": [],
        "n_strikes": [],
        "top_offsets_unix": [],
        "top_scores": [],
    }
    for res in stages:
        n = len(res)
        rows["stage"].extend([res.stage] * n)
        rows["lat"].extend(res.lat.tolist())
        rows["lon"].extend(res.lon.tolist())
        rows["score"].extend(res.score.tolist())
        rows["offset_unix"].extend(res.offset_unix.tolist())
        rows["offset_utc"].extend(
            [utc_iso(datetime.fromtimestamp(t, tz=UTC), milliseconds=True) for t in res.offset_unix]
        )
        rows["n_strikes"].extend(res.n_strikes.tolist())
        rows["top_offsets_unix"].extend(res.top_offsets_unix.tolist())
        rows["top_scores"].extend(res.top_scores.tolist())
    return pa.table(
        {
            "stage": pa.array(rows["stage"], type=pa.string()),
            "lat": pa.array(rows["lat"], type=pa.float64()),
            "lon": pa.array(rows["lon"], type=pa.float64()),
            "score": pa.array(rows["score"], type=pa.float64()),
            "offset_unix": pa.array(rows["offset_unix"], type=pa.float64()),
            "offset_utc": pa.array(rows["offset_utc"], type=pa.string()),
            "n_strikes": pa.array(rows["n_strikes"], type=pa.int64()),
            "top_offsets_unix": pa.array(rows["top_offsets_unix"], type=pa.list_(pa.float64())),
            "top_scores": pa.array(rows["top_scores"], type=pa.list_(pa.float64())),
        }
    )


def _heatmap(coarse: CandidateResults, final: CandidateResults) -> dict[str, object]:
    features: list[dict[str, object]] = []
    for i in range(len(coarse)):
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(coarse.lon[i]), float(coarse.lat[i])],
                },
                "properties": {
                    "stage": coarse.stage,
                    "score": round(float(coarse.score[i]), 4),
                    "offset_unix": float(coarse.offset_unix[i]),
                },
            }
        )
    for rank in range(len(final)):
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(final.lon[rank]), float(final.lat[rank])],
                },
                "properties": {
                    "stage": "final",
                    "rank": rank + 1,
                    "score": round(float(final.score[rank]), 4),
                    "offset_unix": float(final.offset_unix[rank]),
                    "offset_utc": utc_iso(
                        datetime.fromtimestamp(float(final.offset_unix[rank]), tz=UTC), True
                    ),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _overlay(
    observed_fine: Envelope,
    index: StrikeIndex,
    lat: float,
    lon: float,
    offset_unix: float,
    cfg: PipelineConfig,
) -> dict[str, object]:
    step = cfg.refine.step_s
    k = observed_fine.values.size
    models, n = render_models(
        index, np.array([lat]), np.array([lon]), offset_unix, step, k, cfg.model, cfg.search
    )
    modeled = models[0]
    o = observed_fine.values
    scale = float(np.max(o) / np.max(modeled)) if np.max(modeled) > 0 and np.max(o) > 0 else 1.0
    return {
        "lat": lat,
        "lon": lon,
        "offset_unix": offset_unix,
        "offset_utc": utc_iso(datetime.fromtimestamp(offset_unix, tz=UTC), True),
        "step_s": step,
        "n_strikes": int(n[0]),
        "t_s": (np.arange(k) * step).tolist(),
        "observed": o.tolist(),
        "modeled": (modeled * scale).tolist(),
    }


def analyze_thunder(
    recording: str | Path,
    events: LightningEvents,
    priors: ThunderPriors,
    out_dir: str | Path,
    cfg: PipelineConfig | None = None,
    landmask: LandMask | None = None,
    progress: Progress = None,
) -> dict[str, object]:
    """Run the thunder pipeline and write the run folder. Returns the summary dict."""
    cfg = cfg or PipelineConfig()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log = RunLog(out / "run.log", echo=progress)
    t_wall = time.perf_counter()
    rec = Path(recording)
    log.log(f"earwitness {__version__} thunder analysis of {rec.name}")

    samples, rate = read_audio(rec)
    coarse_obs, fine_obs, obs_info = prepare_observed(samples, rate, cfg)
    clip_s = float(obs_info["duration_s"])
    log.log(
        f"recording: {clip_s:.1f} s at {rate} Hz; {obs_info['peak_count']} loudness peaks; "
        f"wind-flagged fraction {float(obs_info['wind_fraction']):.2f}"
    )

    t_start = priors.start.timestamp()
    t_end = priors.end.timestamp()
    window = events.between(t_start - clip_s - 3600.0, t_end + clip_s + 3600.0)
    log.log(
        f"lightning: {len(window)} of {len(events)} events inside the time prior ({events.source})"
    )
    index = StrikeIndex(window)

    lat, lon, region, grid_counts = candidate_grid(window, priors, cfg, landmask, log.log)
    min_strikes = cfg.search.min_strikes
    if min_strikes is None:
        min_strikes = max(3, round(0.6 * int(obs_info["peak_count"])))
    counts = strike_count_prefilter(index, lat, lon, cfg.search.r_max_m, clip_s)
    keep = counts >= min_strikes
    grid_counts["prefilter"] = int(np.count_nonzero(keep))
    log.log(
        f"prefilter: N >= {min_strikes} strikes within {cfg.search.r_max_m / 1000:g} km: "
        f"{grid_counts['prefilter']} candidates"
    )
    lat, lon = lat[keep], lon[keep]

    n_offsets = int(np.floor((t_end - t_start) / cfg.search.step_s)) + 1
    log.log(f"coarse: {lat.size} candidates x {n_offsets} offsets at {cfg.search.step_s:g} s")
    t0 = time.perf_counter()
    coarse = search(
        coarse_obs.values, index, lat, lon, t_start, t_end, cfg.search, cfg.model, "coarse", log.log
    )
    log.log(f"coarse search took {time.perf_counter() - t0:.1f} s")

    stages = [coarse]
    final = CandidateResults.empty("final")
    picks = (
        pick_regions(coarse, cfg.top_k, cfg.refine.min_separation_m)
        if len(coarse)
        else np.zeros(0, dtype=np.int64)
    )
    if cfg.do_refine and picks.size:
        t0 = time.perf_counter()
        stage1, stage2, final = refine(
            fine_obs.values,
            index,
            coarse,
            picks,
            cfg.search,
            cfg.model,
            cfg.refine,
            log.log,
            offset_bounds=(t_start, t_end),
        )
        stages.extend([stage1, stage2, final])
        log.log(f"refinement of {picks.size} regions took {time.perf_counter() - t0:.1f} s")
    elif picks.size:
        final = replace(coarse.subset(picks), stage="final")
        final = final.subset(final.order())
        stages.append(final)

    pq.write_table(_results_table(stages), out / "results.parquet")
    (out / "heatmap.geojson").write_text(json.dumps(_heatmap(coarse, final)), encoding="utf-8")
    (out / "histogram.json").write_text(json.dumps(_histogram(coarse.score)), encoding="utf-8")
    overlays_dir = out / "overlays"
    overlays_dir.mkdir(exist_ok=True)
    for rank in range(min(cfg.overlay_count, len(final))):
        ov = _overlay(
            fine_obs,
            index,
            float(final.lat[rank]),
            float(final.lon[rank]),
            float(final.offset_unix[rank]),
            cfg,
        )
        (overlays_dir / f"rank_{rank + 1:02d}.json").write_text(json.dumps(ov), encoding="utf-8")

    top = []
    for rank in range(len(final)):
        top.append(
            {
                "rank": rank + 1,
                "lat": float(final.lat[rank]),
                "lon": float(final.lon[rank]),
                "score": float(final.score[rank]),
                "offset_unix": float(final.offset_unix[rank]),
                "offset_utc": utc_iso(
                    datetime.fromtimestamp(float(final.offset_unix[rank]), tz=UTC), True
                ),
                "n_strikes": int(final.n_strikes[rank]),
            }
        )
    runtime = time.perf_counter() - t_wall
    summary: dict[str, object] = {
        "schema": "earwitness.thunder-run/1",
        "earwitness_version": __version__,
        "recording": {
            "file": rec.name,
            "sha256": sha256_file(rec),
            "sample_rate_hz": rate,
            "duration_s": clip_s,
        },
        "priors": {
            "start_utc": utc_iso(priors.start),
            "end_utc": utc_iso(priors.end),
            "region": None if priors.region is None else priors.region.as_tuple(),
        },
        "region_used": region.as_tuple(),
        "events": {"source": events.source, "total": len(events), "in_time_prior": len(window)},
        "observed": obs_info,
        "counts": grid_counts,
        "min_strikes": int(min_strikes),
        "n_offsets": n_offsets,
        "coarse_best_score": float(coarse.score.max()) if len(coarse) else None,
        "top": top,
        "runtime_s": runtime,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "priors.json").write_text(json.dumps(summary["priors"], indent=2), encoding="utf-8")
    config = {
        "search": asdict(cfg.search),
        "model": asdict(cfg.model),
        "refine": asdict(cfg.refine),
        "pipeline": {
            "grid_spacing_m": cfg.grid_spacing_m,
            "top_k": cfg.top_k,
            "do_refine": cfg.do_refine,
            "use_land_mask": cfg.use_land_mask,
            "mask_wind": cfg.mask_wind,
            "baseline_percentile": cfg.baseline_percentile,
        },
    }
    (out / "config.toml").write_text(toml_dumps(config), encoding="utf-8")
    log.log(f"done in {runtime:.1f} s; best {top[0] if top else 'none'}")
    return summary


def distance_to(summary: dict[str, object], lat: float, lon: float) -> list[float]:
    """Distances (m) from each ranked result to a reference point, for scoring test cases."""
    top = summary["top"]
    return [
        float(geodesy.horizontal_distance(t["lat"], t["lon"], lat, lon))  # type: ignore[index]
        for t in top  # type: ignore[union-attr]
    ]
