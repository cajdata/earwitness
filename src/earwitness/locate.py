"""One-step locate: a recording, a rough time and a rough place in; plain answers out.

This is what an analyst does across the audit, the spectrogram tools and the run panel, in
one call:

1. Audit the recording for clues (`audit.report`).
2. Measure its propeller flyovers automatically (`aircraft.detect`) and take the strongest
   two, or one when the start time is known, as `events.json`.
3. Fetch what is missing from the cache: the day's adsb.lol flights over the area and GOES GLM
   lightning around the time.
4. Run each method the recording has clues for, into its own run folder, so every answer can
   be checked and re-run like one started by hand.
5. Say how sure each answer is, in words calibrated on the real cases in docs/VALIDATION.md.

The time and the place are priors, as everywhere else: the start time, exact or rough, or
just the day; and a region, usually a point with a radius. Times are UTC here; callers convert
local times at their edge (`audit.clock` reads the start from the file itself).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from earwitness import __version__
from earwitness.core.grid import BBox
from earwitness.project import sha256_file, utc_iso

Progress = Callable[[str], None] | None

SCHEMA = "earwitness.locate/1"
FETCH_MARGIN_M = 30000.0
"""Flights are cached for the search region widened by this much: an aircraft heard at the
edge of the area can fly well outside it, and a wider cache serves a nearby search later."""
THUNDER_FETCH_PAD_S = 300.0
"""Lightning fetched beyond a known clip: thunder from 25 km arrives about 75 s late."""
THUNDER_CLOCK_PAD_S = 60.0
"""A known start still lets the thunder search slide a minute either way: its coarse step is
5 s and the refinement needs room around the best offset."""


@dataclass(frozen=True)
class TimePrior:
    """When the recording started: a known moment with a tolerance, or only the day(s)."""

    window_start: datetime
    window_end: datetime
    """UTC window the clip's start lies in."""
    start: datetime | None = None
    tolerance_s: float | None = None
    """Known start and how far off it may be; None when only the window is known."""

    @classmethod
    def at(cls, start: datetime, tolerance_s: float = 2.0) -> TimePrior:
        """A known start, good to `tolerance_s` (2 s for a phone clock, minutes when rough)."""
        if start.tzinfo is None:
            raise ValueError("the start time must be timezone-aware")
        start = start.astimezone(UTC)
        pad = timedelta(seconds=tolerance_s)
        return cls(start - pad, start + pad, start, float(tolerance_s))

    @classmethod
    def days(cls, first: date, last: date | None = None) -> TimePrior:
        """Only the UTC day (or days, inclusive) the clip was recorded on."""
        lo = datetime(first.year, first.month, first.day, tzinfo=UTC)
        end = last or first
        hi = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
        if hi <= lo:
            raise ValueError("the last day is before the first")
        return cls(lo, hi)

    @property
    def exact(self) -> bool:
        """True when the start is known to a few seconds, enough for one flyover."""
        return self.start is not None and (self.tolerance_s or 0.0) <= 5.0

    def flight_days(self, duration_s: float) -> list[date]:
        """UTC days whose flight files the search loads: with a known start, every day the
        clip itself touches; with only the day(s), those days, as `analyze aircraft --date`
        loads them (a day's file is several GB, so neighbours are not fetched on a guess)."""
        if self.start is None:
            lo, hi = self.window_start, self.window_end - timedelta(seconds=1)
        else:
            lo, hi = self.window_start, self.window_end + timedelta(seconds=duration_s)
        days = []
        day = lo.date()
        while day <= hi.date():
            days.append(day)
            day += timedelta(days=1)
        return days

    def search_window(self) -> tuple[datetime, datetime]:
        """The UTC days the aircraft priors span (whole days, as the CLI's --date)."""
        lo = self.window_start.replace(hour=0, minute=0, second=0, microsecond=0)
        hi = self.window_end.replace(hour=0, minute=0, second=0, microsecond=0)
        if hi < self.window_end:
            hi += timedelta(days=1)
        return lo, max(hi, lo + timedelta(days=1))

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form."""
        return {
            "window_start_utc": utc_iso(self.window_start),
            "window_end_utc": utc_iso(self.window_end),
            "start_utc": None if self.start is None else utc_iso(self.start, True),
            "tolerance_s": self.tolerance_s,
        }


def region_around(lat: float, lon: float, radius_m: float) -> BBox:
    """The square region that holds a circle of `radius_m` around a point."""
    if radius_m <= 0:
        raise ValueError("the radius must be positive")
    return BBox.around(lat, lon, radius_m)


@dataclass(frozen=True)
class LocateOptions:
    """What to run and how; defaults are the settings validated in docs/VALIDATION.md."""

    methods: tuple[str, ...] = ("aircraft", "thunder")
    max_events: int = 2
    fetch: bool = True
    """Download missing flight days and lightning hours; False uses the cache only."""
    variant: str = "prod"
    terrain: bool = True
    workers: int = 1
    """Processes that score aircraft candidates (0: one per CPU)."""
    max_pairs: int = 3_000_000
    """Flight-pair hints the aircraft prefilter keeps; pattern traffic needs many."""
    max_candidates: int = 5_000_000
    """Candidate cells scored before a random cap; the real cases were scored uncapped, and a
    cap can drop the true cell."""
    thunder_rmax_km: float = 25.0
    force_thunder: bool = False
    """Run the thunder search even when the audit heard no thunder."""


def confidence(method: str, margin: float | None) -> tuple[str, str]:
    """A confidence level and one plain sentence for an answer's margin.

    The margin is the best answer's score minus the runner-up's. In docs/VALIDATION.md every
    located aircraft case won by 1.6 or more and every failed one by 0.2 or less, so aircraft
    answers are clear, probable or unsettled. The one located thunder case won by 0.084 and the
    failures by 0.031 or less, so a thunder answer is at best possible.
    """
    if margin is None:
        if method == "aircraft":
            return (
                "probable",
                "Only one answer passed the filters, so there is no rival to compare it with;"
                " check the flyovers it measured.",
            )
        return (
            "possible",
            "Only one storm position passed the filters, so there is no rival to compare it"
            " with, and thunder answers are often wrong unless the storm was close and sparse.",
        )
    if method == "aircraft":
        if margin >= 1.0:
            return "clear", "Clear: no other flight pair or place fits nearly as well."
        if margin >= 0.3:
            return "probable", "Probable: a rival answer fits almost as well; check both."
        return (
            "unsettled",
            "Not settled: other places fit about as well. A known start time, another flyover"
            " or a smaller area would help.",
        )
    if margin >= 0.06:
        return (
            "possible",
            "Possible: this storm position fits best by a clear step, but thunder answers are"
            " often wrong unless the storm was close and sparse.",
        )
    return (
        "unsettled",
        "Not settled: other storm positions fit about as well, as in most thunder recordings"
        " tested so far.",
    )


@dataclass
class MethodResult:
    """What one method did: ran with an answer, or why it was skipped or failed."""

    method: str
    status: str
    """ran, skipped or failed."""
    reason: str = ""
    run_dir: str | None = None
    lat: float | None = None
    lon: float | None = None
    score: float | None = None
    margin: float | None = None
    level: str | None = None
    sentence: str | None = None
    start_utc: str | None = None
    runtime_s: float | None = None
    top: list[dict[str, object]] = field(default_factory=list)


LEVELS = {"clear": 4, "probable": 3, "possible": 2, "unsettled": 1}


def _answer(result: MethodResult, summary: dict[str, object], start_key: str) -> MethodResult:
    top = list(summary.get("top") or [])  # type: ignore[arg-type]
    result.runtime_s = float(summary.get("runtime_s") or 0.0)  # type: ignore[arg-type]
    if not top:
        result.status = "ran"
        result.reason = "the search found no candidate that fits"
        result.level, result.sentence = "unsettled", "No answer: nothing fitted."
        return result
    best = top[0]
    result.lat, result.lon = float(best["lat"]), float(best["lon"])  # type: ignore[arg-type]
    result.score = float(best["score"])  # type: ignore[arg-type]
    result.start_utc = str(best.get(start_key) or "") or None
    result.margin = (
        float(top[0]["score"]) - float(top[1]["score"]) if len(top) > 1 else None  # type: ignore[arg-type]
    )
    result.level, result.sentence = confidence(result.method, result.margin)
    result.top = [
        {
            "rank": t.get("rank"),
            "lat": t.get("lat"),
            "lon": t.get("lon"),
            "score": t.get("score"),
            "start_utc": t.get(start_key),
        }
        for t in top[:5]
    ]
    result.status = "ran"
    return result


def ensure_flights(
    day: date,
    region: BBox,
    cache: Path | None,
    variant: str,
    fetch: bool,
    progress: Progress,
) -> list[object]:
    """The day's prepared flights over the region, downloading the day first if needed."""
    from earwitness.aircraft.prep import prepare_flights
    from earwitness.data.adsb_lol import build_day, cached_regions, load_day
    from earwitness.data.aircraft_types import TypeTable
    from earwitness.data.cache import cache_root

    say = progress or (lambda _m: None)

    def covering() -> list[tuple[BBox, Path]]:
        return [
            (b, p) for b, p in cached_regions(cache, day, variant) if b.contains(*region.center)
        ]

    options = covering()
    if not options:
        if not fetch:
            raise RuntimeError(f"no cached adsb.lol day covering the area for {day}")
        say(
            f"downloading the adsb.lol flight records for {day} (several GB, once per day;"
            " this is the slow part)"
        )
        build_day(day, region.expand(FETCH_MARGIN_M), cache_root(cache), variant, log=progress)
        options = covering()
        if not options:
            raise RuntimeError(f"the adsb.lol day for {day} did not cover the area")
    cached_bbox, _ = options[-1]
    table = load_day(day, cached_bbox, cache, variant, sub_bbox=region.expand(12000.0))
    try:
        types = TypeTable.load(cache)
    except RuntimeError as exc:
        say(f"warning: {exc}; engine classes will be unknown")
        types = None
    flights = prepare_flights(table, types)
    say(f"{table.num_rows} flight points, {len(flights)} airborne legs over the area")
    return list(flights)


def run_aircraft_step(
    recording: Path,
    events: object,
    when: TimePrior,
    region: BBox,
    out_dir: Path,
    cache: Path | None,
    options: LocateOptions,
    progress: Progress,
    flights: list[object] | None = None,
    duration_s: float = 0.0,
) -> MethodResult:
    """Fetch the flights (unless given) and run the aircraft pipeline on measured events."""
    from earwitness.aircraft.funnel import FunnelConfig
    from earwitness.aircraft.pipeline import (
        AircraftPipelineConfig,
        AircraftPriors,
        analyze_aircraft,
    )

    result = MethodResult("aircraft", "failed")
    start, end = when.search_window()
    if flights is None:
        flights = []
        for day in when.flight_days(duration_s):
            try:
                flights += ensure_flights(
                    day, region, cache, options.variant, options.fetch, progress
                )
            except RuntimeError:
                if day == start.date():
                    raise
                # an edge day not yet published or not cached: the main day still runs
                if progress:
                    progress(f"no flight records for {day}; searching without them")
    terrain = None
    if options.terrain:
        from earwitness.data.dem import Terrain

        terrain = Terrain(cache)
    cfg = AircraftPipelineConfig(
        funnel=FunnelConfig(silence_mode="soft", max_pairs=options.max_pairs),
        use_terrain=options.terrain,
        workers=options.workers,
        max_candidates=options.max_candidates,
    )
    run_dir = out_dir
    summary = analyze_aircraft(
        recording,
        events,  # type: ignore[arg-type]
        flights,  # type: ignore[arg-type]
        AircraftPriors(start=start, end=end, region=region),
        run_dir,
        cfg,
        terrain,
        progress=progress,
    )
    events.save(run_dir / "events.json")  # type: ignore[attr-defined]
    result.run_dir = str(run_dir)
    return _answer(result, summary, "t_start_utc")


def run_thunder_step(
    recording: Path,
    when: TimePrior,
    region: BBox,
    out_dir: Path,
    cache: Path | None,
    options: LocateOptions,
    progress: Progress,
    lightning: object | None = None,
    duration_s: float = 0.0,
) -> MethodResult:
    """Load GLM flashes around the time (unless given) and run the thunder pipeline, if any
    flashed near the area. With a known start only the clip's own hours are fetched: a flash
    is heard within about 75 s at 25 km, so flashes an hour away cannot matter."""
    from earwitness.data.glm import load_flashes, resolve_satellite
    from earwitness.data.landmask import LandMask
    from earwitness.thunder.pipeline import PipelineConfig, ThunderPriors, analyze_thunder
    from earwitness.thunder.search import SearchConfig

    result = MethodResult("thunder", "skipped")
    if when.start is not None:
        pad = timedelta(seconds=(when.tolerance_s or 0.0) + THUNDER_CLOCK_PAD_S)
        start, end = when.start - pad, when.start + pad
    else:
        start, end = when.window_start, when.window_end
    source = "the lightning given"
    if lightning is not None:
        flashes = lightning
    else:
        satellite = resolve_satellite("auto", start.date(), region)
        source = f"GOES {satellite}"
        if not options.fetch:
            result.reason = "lightning data is downloaded per hour; fetching is off"
            return result
        reach = region.expand(options.thunder_rmax_km * 1000.0)
        if when.start is not None:
            lo = start - timedelta(seconds=THUNDER_FETCH_PAD_S)
            hi = end + timedelta(seconds=duration_s + THUNDER_FETCH_PAD_S)
        else:
            lo, hi = start - timedelta(hours=1), end + timedelta(hours=1)
        flashes = load_flashes(satellite, lo, hi, reach, cache, log=progress)
    if len(flashes) == 0:  # type: ignore[arg-type]
        result.reason = (
            f"no lightning within {options.thunder_rmax_km:g} km of the area in that time"
            f" ({source})"
        )
        return result
    cfg = PipelineConfig(search=SearchConfig(r_max_m=options.thunder_rmax_km * 1000.0))
    landmask = LandMask.natural_earth(cache, "50m") if lightning is None else None
    run_dir = out_dir
    summary = analyze_thunder(
        recording,
        flashes,  # type: ignore[arg-type]
        ThunderPriors(start=start, end=end, region=region, region_geometry=None),
        run_dir,
        cfg,
        landmask,
        progress=progress,
    )
    result.run_dir = str(run_dir)
    return _answer(result, summary, "offset_utc")


def locate(
    recording: str | Path,
    when: TimePrior,
    region: BBox,
    out_dir: str | Path,
    cache: Path | None = None,
    options: LocateOptions | None = None,
    progress: Progress = None,
    flights: list[object] | None = None,
    lightning: object | None = None,
    run_dirs: Callable[[str], Path] | None = None,
) -> dict[str, object]:
    """Audit, measure, fetch and search; write `locate.json` and return it.

    Each method's run folder is `run_dirs(method)`, by default `out_dir / method`; the UI puts
    them beside its other runs so its Results view lists them. `flights` (prepared `Flight`s)
    and `lightning` (`LightningEvents`) replace the downloads, for synthetic cases and tests.
    """
    from earwitness.aircraft.detect import (
        DetectConfig,
        auto_events,
        detect_flyovers,
        detection_spectrogram,
    )
    from earwitness.audio.decode import read_audio, resample
    from earwitness.audit.report import audit_file

    options = options or LocateOptions()
    say = progress or (lambda _m: None)
    rec = Path(recording)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    folder_for = run_dirs or (lambda method: out / method)
    t_wall = time.perf_counter()
    say(f"earwitness {__version__}: locating {rec.name}")

    samples, rate = read_audio(rec)
    duration = samples.size / rate
    from earwitness.aircraft.detect import RATE_HZ

    low = resample(samples, rate, RATE_HZ) if rate != RATE_HZ else samples
    del samples
    say(f"{duration:.0f} s of audio; checking it for clues")
    audit = audit_file(rec, low, RATE_HZ)
    clues = {c.kind: c for c in audit.clues}

    cfg = DetectConfig()
    detection = detect_flyovers(detection_spectrogram(low, RATE_HZ, cfg), cfg)
    flyovers = [f.summary() for f in detection.flyovers]
    say(
        f"{len(detection.flyovers)} propeller flyover(s) measured"
        + (
            ": " + ", ".join(f"{f['heard_s']:.0f} s" for f in flyovers[: options.max_events])
            if flyovers
            else ""
        )
    )
    results: list[MethodResult] = []
    used: list[str] = []

    if "aircraft" in options.methods:
        events = auto_events(
            detection,
            max_events=options.max_events,
            known_time=when.start if when.exact else None,
            known_time_tolerance_s=when.tolerance_s or 2.0,
            recording={"file": rec.name, "sha256": sha256_file(rec)},
        )
        if when.start is not None and not when.exact and events is not None:
            events.known_time = when.start
            events.known_time_tolerance_s = float(when.tolerance_s or 0.0)
        if events is None:
            reason = (
                "no propeller flyover was measured"
                if not detection.flyovers
                else "one flyover needs a known start time (two need only the day)"
            )
            results.append(MethodResult("aircraft", "skipped", reason))
        else:
            used = [e.id for e in events.events]
            try:
                results.append(
                    run_aircraft_step(
                        rec,
                        events,
                        when,
                        region,
                        folder_for("aircraft"),
                        cache,
                        options,
                        progress,
                        flights,
                        duration,
                    )
                )
            except (RuntimeError, ValueError, OSError) as exc:
                results.append(MethodResult("aircraft", "failed", str(exc)))

    if "thunder" in options.methods:
        heard = clues.get("thunder")
        settled = any(r.method == "aircraft" and r.level == "clear" for r in results)
        if heard is not None and not heard.present and not options.force_thunder:
            results.append(MethodResult("thunder", "skipped", "no thunder-like bursts heard"))
        elif settled and not options.force_thunder:
            results.append(
                MethodResult(
                    "thunder",
                    "skipped",
                    "the aircraft answer is clear, and thunder places a recording far less"
                    " precisely",
                )
            )
        else:
            try:
                results.append(
                    run_thunder_step(
                        rec,
                        when,
                        region,
                        folder_for("thunder"),
                        cache,
                        options,
                        progress,
                        lightning,
                        duration,
                    )
                )
            except (RuntimeError, ValueError, OSError) as exc:
                results.append(MethodResult("thunder", "failed", str(exc)))

    ran = [r for r in results if r.status == "ran" and r.lat is not None]
    best = max(
        ran, key=lambda r: (LEVELS.get(r.level or "", 0), r.method == "aircraft"), default=None
    )
    summary: dict[str, object] = {
        "schema": SCHEMA,
        "earwitness_version": __version__,
        "recording": {"file": rec.name, "sha256": sha256_file(rec), "duration_s": duration},
        "time_prior": when.to_dict(),
        "region": list(region.as_tuple()),
        "clues": {
            k: {"present": c.present, "confidence": c.confidence, "summary": c.summary}
            for k, c in clues.items()
        },
        "flyovers": flyovers,
        "events_used": used,
        "methods": [asdict(r) for r in results],
        "answer": None
        if best is None
        else {
            "method": best.method,
            "lat": best.lat,
            "lon": best.lon,
            "level": best.level,
            "sentence": best.sentence,
            "run_dir": best.run_dir,
        },
        "runtime_s": time.perf_counter() - t_wall,
    }
    (out / "locate.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    say(f"done in {summary['runtime_s']:.0f} s")
    return summary
