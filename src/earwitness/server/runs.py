"""What a UI run does: the same steps as the CLI handlers, driven by request models.

Each function here takes a validated request and the job's progress callable and does what the
matching `earwitness analyze` or `earwitness data` command does, with the same engine calls in
the same order, so a run started from the UI reproduces from its folder exactly like one started
from the CLI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field
from shapely.geometry.base import BaseGeometry

from earwitness.core.grid import BBox, geometry_bbox, geometry_from_geojson
from earwitness.project import parse_utc, parse_utc_date
from earwitness.server.jobs import Progress
from earwitness.server.workspace import Workspace

Bounds = list[float]


class ThunderRunRequest(BaseModel):
    """`earwitness analyze thunder` as a request."""

    recording_id: str
    date: str
    date_end: str | None = None
    time_window: list[str] | None = None
    """Optional narrower UTC window for the clip start, two ISO timestamps."""
    bbox: Bounds = Field(min_length=4, max_length=4)
    region_geojson: dict[str, object] | None = None
    satellite: str = "auto"
    step_s: float = 5.0
    rmax_km: float = 25.0
    grid_km: float = 5.0
    top: int = 10
    refine: bool = True
    land_mask: bool = True
    bearing_weight: float = 0.0
    mask_wind: bool = False
    score: str = "cosine"
    min_strikes: int | None = None


class AircraftRunRequest(BaseModel):
    """`earwitness analyze aircraft` as a request; `events` is the events.json content."""

    recording_id: str
    date: str
    date_end: str | None = None
    time_window: list[str] | None = None
    bbox: Bounds = Field(min_length=4, max_length=4)
    events: dict[str, object]
    known_time_utc: str | None = None
    known_time_tolerance_s: float | None = None
    variant: str = "prod"
    silence: str = "hard"
    terrain: bool = True
    top: int = 10
    max_candidates: int = 30000
    harmonics: int = 1


class AdsbFetchRequest(BaseModel):
    """`earwitness data adsb` as a request."""

    date: str
    bbox: Bounds = Field(min_length=4, max_length=4)
    variant: str = "prod"


class EstimateRequest(BaseModel):
    """What the run panel asks before starting: how big is this search?"""

    method: str
    date: str
    date_end: str | None = None
    bbox: Bounds = Field(min_length=4, max_length=4)
    known_time: bool = False
    step_s: float = 5.0
    grid_km: float = 5.0


def time_window(
    date: str, date_end: str | None, window: list[str] | None = None
) -> tuple[datetime, datetime]:
    """[first day 00:00, last day 24:00) in UTC, or the explicit window when one is given."""
    if window:
        if len(window) != 2:
            raise ValueError("time_window needs exactly two timestamps")
        start, end = parse_utc(window[0]), parse_utc(window[1])
    else:
        start = parse_utc_date(date)
        end = (parse_utc_date(date_end) if date_end else start) + timedelta(days=1)
    if end <= start:
        raise ValueError("the end date is before the start date")
    return start, end


def region_prior(
    bbox: Bounds, region_geojson: dict[str, object] | None
) -> tuple[BBox, BaseGeometry | None]:
    """The search region: a polygon's bounds when one is given, else the bbox itself."""
    if region_geojson is not None:
        geometry = geometry_from_geojson(region_geojson)
        return geometry_bbox(geometry), geometry
    return BBox(*bbox), None


def run_thunder(ws: Workspace, req: ThunderRunRequest, progress: Progress) -> dict[str, object]:
    """Load GLM flashes for the priors and run the thunder pipeline into a new run folder."""
    from earwitness.data.glm import load_flashes, resolve_satellite
    from earwitness.data.landmask import LandMask
    from earwitness.thunder.pipeline import PipelineConfig, ThunderPriors, analyze_thunder
    from earwitness.thunder.search import SearchConfig

    rec = ws.recording(req.recording_id)
    start, end = time_window(req.date, req.date_end, req.time_window)
    region, geometry = region_prior(req.bbox, req.region_geojson)
    satellite = resolve_satellite(req.satellite, start.date(), region)
    margin = timedelta(hours=1)
    progress(
        f"GLM {satellite}: loading flashes for {start:%Y-%m-%d} to {end:%Y-%m-%d} over {region}"
    )
    events = load_flashes(
        satellite,
        start - margin,
        end + margin,
        region.expand(req.rmax_km * 1000.0),
        ws.cache_override,
        log=progress,
    )
    if len(events) == 0:
        raise RuntimeError("no lightning events in the prior window; widen the dates or region")
    progress(f"{len(events)} flashes in the window")
    landmask = LandMask.natural_earth(ws.cache_override, "50m") if req.land_mask else None
    cfg = PipelineConfig(
        search=SearchConfig(
            step_s=req.step_s,
            r_max_m=req.rmax_km * 1000.0,
            min_strikes=req.min_strikes,
            bearing_weight=req.bearing_weight,
            score_mode=req.score,
        ),
        grid_spacing_m=req.grid_km * 1000.0,
        top_k=req.top,
        do_refine=req.refine,
        use_land_mask=req.land_mask,
        mask_wind=req.mask_wind,
    )
    out = ws.new_run_dir(rec.path.stem, "thunder")
    priors = ThunderPriors(start=start, end=end, region=region, region_geometry=geometry)
    summary = analyze_thunder(rec.path, events, priors, out, cfg, landmask, progress=progress)
    return {
        "run_id": out.name,
        "method": "thunder",
        "top": summary["top"],
        "runtime_s": summary["runtime_s"],
    }


def load_cached_flights(
    ws: Workspace, day: datetime, region: BBox, variant: str, progress: Progress
) -> list[object]:
    """Flights from the cached adsb.lol day that covers the region, as the CLI loads them."""
    from earwitness.aircraft.prep import prepare_flights
    from earwitness.data.adsb_lol import cached_regions, load_day
    from earwitness.data.aircraft_types import TypeTable

    options = [
        (b, p)
        for b, p in cached_regions(ws.cache_override, day.date(), variant)
        if b.contains(*region.center)
    ]
    if not options:
        raise RuntimeError(
            f"no cached adsb.lol day covering the region for {day:%Y-%m-%d}; fetch it from the"
            " data panel (or run `earwitness data adsb`) first"
        )
    cached_bbox, _ = options[-1]
    progress(f"adsb.lol {variant} {day:%Y-%m-%d}: loading cached region {cached_bbox}")
    table = load_day(
        day.date(), cached_bbox, ws.cache_override, variant, sub_bbox=region.expand(12000.0)
    )
    try:
        types = TypeTable.load(ws.cache_override)
    except RuntimeError as exc:
        progress(f"warning: {exc}; engine classes will be unknown")
        types = None
    flights = prepare_flights(table, types)
    progress(f"{table.num_rows} points, {len(flights)} airborne legs")
    return list(flights)


def run_aircraft(ws: Workspace, req: AircraftRunRequest, progress: Progress) -> dict[str, object]:
    """Build the events and flights for the priors and run the aircraft pipeline."""
    from earwitness.aircraft.funnel import FunnelConfig
    from earwitness.aircraft.pipeline import (
        AircraftPipelineConfig,
        AircraftPriors,
        analyze_aircraft,
    )
    from earwitness.aircraft.score import ScoreConfig
    from earwitness.audio.events import EventSet

    rec = ws.recording(req.recording_id)
    events = EventSet.from_dict(req.events)
    events.recording = {"file": rec.path.name, "sha256": rec.sha256}
    if req.known_time_utc:
        events.known_time = parse_utc(req.known_time_utc)
    if req.known_time_tolerance_s is not None:
        events.known_time_tolerance_s = req.known_time_tolerance_s
    start, end = time_window(req.date, req.date_end, req.time_window)
    region, _ = region_prior(req.bbox, None)
    flights = load_cached_flights(ws, start, region, req.variant, progress)
    terrain = None
    if req.terrain:
        from earwitness.data.dem import Terrain

        terrain = Terrain(ws.cache_override)
    cfg = AircraftPipelineConfig(
        funnel=FunnelConfig(silence_mode=req.silence),
        score=ScoreConfig(harmonics=req.harmonics),
        top_pairs=req.top,
        max_candidates=req.max_candidates,
        use_terrain=req.terrain,
    )
    out = ws.new_run_dir(rec.path.stem, "aircraft")
    priors = AircraftPriors(start=start, end=end, region=region)
    summary = analyze_aircraft(
        rec.path, events, flights, priors, out, cfg, terrain, progress=progress
    )  # type: ignore[arg-type]
    events.save(out / "events.json")
    return {
        "run_id": out.name,
        "method": "aircraft",
        "mode": summary.get("mode"),
        "top": summary["top"],
        "runtime_s": summary["runtime_s"],
    }


def fetch_adsb(ws: Workspace, req: AdsbFetchRequest, progress: Progress) -> dict[str, object]:
    """Download and convert one adsb.lol day for a region into the cache."""
    from earwitness.data.adsb_lol import build_day, day_cache_path
    from earwitness.data.cache import cache_root

    bbox = BBox(*req.bbox)
    day = parse_utc_date(req.date).date()
    root = cache_root(ws.cache_override)
    dest = day_cache_path(root, day, bbox, req.variant)
    if dest.exists():
        progress(f"already cached: {dest}")
        return {"path": str(dest), "cached": True}
    stats = build_day(day, bbox, root, req.variant, log=progress)
    progress(
        f"{stats.traces_read} traces read; {stats.aircraft_kept} aircraft and"
        f" {stats.points_kept} points kept"
    )
    return {
        "path": str(dest),
        "cached": False,
        "traces_read": stats.traces_read,
        "aircraft_kept": stats.aircraft_kept,
        "points_kept": stats.points_kept,
    }


def data_status(ws: Workspace, date: str, bbox: Bounds, variant: str = "prod") -> dict[str, object]:
    """What is already in the cache for these priors, for the data panel."""
    from earwitness.data.adsb_lol import cached_regions
    from earwitness.data.aircraft_types import TypeTable
    from earwitness.data.glm import hour_cache_path, resolve_satellite

    region = BBox(*bbox)
    day = parse_utc_date(date)
    regions = [
        {"bbox": list(b.as_tuple()), "path": str(p), "covers": bool(b.contains(*region.center))}
        for b, p in cached_regions(ws.cache_override, day.date(), variant)
    ]
    try:
        TypeTable.load(ws.cache_override, allow_download=False)
        types_cached = True
    except RuntimeError:
        types_cached = False
    satellite = resolve_satellite("auto", day.date(), region)
    hours = sum(
        hour_cache_path(ws.cache_root, satellite, day.date(), h).exists() for h in range(24)
    )
    return {
        "adsb": {
            "variant": variant,
            "regions": regions,
            "covered": any(r["covers"] for r in regions),
            "types_cached": types_cached,
        },
        "glm": {"satellite": satellite, "hours_cached": hours},
        "cache_root": str(ws.cache_root),
    }


def _duration_text(seconds: float) -> str:
    if seconds < 90:
        return f"about {max(seconds, 5):.0f} s"
    if seconds < 5400:
        return f"about {seconds / 60:.0f} min"
    return f"about {seconds / 3600:.1f} h"


def estimate(req: EstimateRequest) -> dict[str, object]:
    """A rough size and time for the search, from the region, the dates and the mode.

    The constants come from the acceptance runs in docs/VALIDATION.md on one laptop core: an
    aircraft two-event search over 1.8 million km2 and a whole day took 3.5 to 39 minutes
    (median about 12), a single-event known-time search about a minute. They are a guide to
    whether to narrow the priors, not a promise.
    """
    region = BBox(*req.bbox)
    start, end = time_window(req.date, req.date_end)
    days = max((end - start).total_seconds() / 86400.0, 1e-3)
    area = region.area_km2()
    if req.method == "thunder":
        cells = area / max(req.grid_km, 0.1) ** 2
        offsets = days * 86400.0 / max(req.step_s, 0.1)
        seconds = max(cells * offsets * 2.5e-6, 10.0)
        return {
            "method": "thunder",
            "area_km2": round(area),
            "days": round(days, 2),
            "coarse_cells": round(cells),
            "start_times": round(offsets),
            "seconds": round(seconds),
            "text": (
                f"{cells:,.0f} coarse cells and {offsets:,.0f} start times over {area:,.0f} km2;"
                f" {_duration_text(seconds)} for the coarse pass on one core"
            ),
        }
    base = 60.0 if req.known_time else 720.0
    seconds = max(base * (area / 1.8e6) * days, 20.0)
    return {
        "method": "aircraft",
        "area_km2": round(area),
        "days": round(days, 2),
        "seconds": round(seconds),
        "text": (
            f"{area:,.0f} km2 over {days:g} day(s) in"
            f" {'single-event known-time' if req.known_time else 'two-event'} mode;"
            f" {_duration_text(seconds)} on one core, most of it in the funnel"
        ),
    }


def summary_path(run_dir: Path) -> Path:
    """Where a run keeps its summary."""
    return run_dir / "summary.json"
