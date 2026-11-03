"""Refinement: re-run the best coarse regions on finer grids with a 1 s offset step."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray

from earwitness.core import geodesy
from earwitness.core.grid import local_grid
from earwitness.thunder.model import ThunderConfig
from earwitness.thunder.search import (
    CandidateResults,
    Progress,
    SearchConfig,
    StrikeIndex,
    search,
)

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class RefineConfig:
    """Grid and offset windows for the two refinement stages."""

    stage1_half_extent_m: float = 5000.0
    stage1_spacing_m: float = 1000.0
    stage1_offset_window_s: float = 60.0
    stage2_half_extent_m: float = 1000.0
    stage2_spacing_m: float = 200.0
    stage2_offset_window_s: float = 10.0
    step_s: float = 1.0
    min_separation_m: float = 15000.0
    """Minimum distance between the coarse regions chosen for refinement."""


def pick_regions(
    results: CandidateResults, top_k: int, min_separation_m: float
) -> NDArray[np.int64]:
    """Indices of the top-scoring candidates at least `min_separation_m` apart (greedy)."""
    chosen: list[int] = []
    for i in results.order():
        if len(chosen) >= top_k:
            break
        if results.score[i] <= 0.0:
            break
        if chosen:
            d = geodesy.horizontal_distance(
                results.lat[chosen],
                results.lon[chosen],
                float(results.lat[i]),
                float(results.lon[i]),
            )
            if np.min(d) < min_separation_m:
                continue
        chosen.append(int(i))
    return np.asarray(chosen, dtype=np.int64)


def _best(results: CandidateResults) -> CandidateResults:
    return results.subset(np.asarray([int(results.order()[0])], dtype=np.int64))


def refine(
    observed_fine: FloatArray,
    index: StrikeIndex,
    coarse: CandidateResults,
    picks: NDArray[np.int64],
    search_cfg: SearchConfig,
    model_cfg: ThunderConfig,
    refine_cfg: RefineConfig | None = None,
    progress: Progress = None,
) -> tuple[CandidateResults, CandidateResults, CandidateResults]:
    """Refine each picked coarse region through 1 km and 200 m grids.

    `observed_fine` is the observed envelope at `refine_cfg.step_s`. Returns (all stage-1
    candidates, all stage-2 candidates, one final best per region sorted by score).
    """
    refine_cfg = refine_cfg or RefineConfig()
    fine_cfg = replace(search_cfg, step_s=refine_cfg.step_s)
    stage1_parts: list[CandidateResults] = []
    stage2_parts: list[CandidateResults] = []
    finals: list[CandidateResults] = []
    for rank, i in enumerate(picks, start=1):
        lat0, lon0, tau0 = float(coarse.lat[i]), float(coarse.lon[i]), float(coarse.offset_unix[i])
        lat1, lon1 = local_grid(
            lat0, lon0, refine_cfg.stage1_half_extent_m, refine_cfg.stage1_spacing_m
        )
        w1 = refine_cfg.stage1_offset_window_s
        s1 = search(
            observed_fine,
            index,
            lat1,
            lon1,
            tau0 - w1,
            tau0 + w1,
            fine_cfg,
            model_cfg,
            "refine-1km",
        )
        stage1_parts.append(s1)
        b1 = _best(s1)
        lat2, lon2 = local_grid(
            float(b1.lat[0]),
            float(b1.lon[0]),
            refine_cfg.stage2_half_extent_m,
            refine_cfg.stage2_spacing_m,
        )
        w2 = refine_cfg.stage2_offset_window_s
        tau1 = float(b1.offset_unix[0])
        s2 = search(
            observed_fine,
            index,
            lat2,
            lon2,
            tau1 - w2,
            tau1 + w2,
            fine_cfg,
            model_cfg,
            "refine-200m",
        )
        stage2_parts.append(s2)
        finals.append(_best(s2))
        if progress:
            progress(
                f"refine region {rank}/{len(picks)}: coarse {coarse.score[i]:.3f} -> "
                f"{float(finals[-1].score[0]):.3f}"
            )
    if not finals:
        empty = CandidateResults.empty("refine-200m")
        return empty, empty, empty
    stage1 = CandidateResults.concat(stage1_parts, "refine-1km")
    stage2 = CandidateResults.concat(stage2_parts, "refine-200m")
    final = CandidateResults.concat(finals, "final")
    return stage1, stage2, final.subset(final.order())
