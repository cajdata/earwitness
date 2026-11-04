"""The UI's HTTP API over the engine, and the built front end as static files.

Every route is a thin adapter: validate the request, call the engine the way the CLI does,
shape the answer for the browser. Nothing here decides anything about the physics. The server
binds to localhost only, and every API request must carry the session token the launcher puts
in the page URL, so a page in another browser tab (or a DNS-rebinding site) cannot drive it or
read local files through it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from earwitness import __version__
from earwitness.audio.decode import ffmpeg_path
from earwitness.server import runs
from earwitness.server.workspace import Workspace, envelope_view, spectrogram_view

STATIC_DIR = Path(__file__).with_name("static")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

MISSING_UI = """<!doctype html><meta charset="utf-8"><title>Earwitness</title>
<body style="font: 15px/1.5 system-ui; max-width: 40em; margin: 4em auto; color: #222">
<h1>Earwitness API is running</h1>
<p>The front end has not been built in this checkout. Build it once with</p>
<pre>cd ui
npm install
npm run build</pre>
<p>and reload this page, or run <code>npm run dev</code> in <code>ui/</code> for live
development against this server.</p></body>"""


class OpenRequest(BaseModel):
    """Open a local recording by path."""

    path: str


class RidgeRequest(BaseModel):
    """Auto-trace the brightest line in a time and frequency box."""

    f_lo: float
    f_hi: float
    t_lo: float | None = None
    t_hi: float | None = None
    min_db: float = 3.0
    nfft: int = 2048
    hop: int = 512
    whitened: bool = True
    """Trace the whitened view (sweeping lines) or the raw power (steady tones)."""
    follow: bool = True
    """Follow the line outward from the brightest frame and stop where it breaks, instead of
    taking every bright frame in the box; a Doppler line is continuous, noise peaks are not."""
    max_step: float = 0.04
    """Largest frequency change between accepted frames, as a fraction, when following."""
    max_gap_s: float = 1.5
    """How long the line may drop out before following stops."""
    min_margin_db: float = 2.0
    """When following, a frame must also be this far above the typical frame of the box in a
    2 s average; isolated noise peaks are bright for one frame, a line stays bright."""


class PreviewRequest(BaseModel):
    """The modeled curve for slider values, sampled over a time window."""

    f0: float
    v: float
    d: float
    t0: float
    t_lo: float
    t_hi: float
    step_s: float = 0.25
    harmonics: int = 1


class FitRequest(BaseModel):
    """Fit (f0, v, d, t0) to traced points."""

    points: list[list[float]] = Field(min_length=5)
    """[[t_s, f_hz], ...]"""


def json_safe(value: object) -> object:
    """The same structure with NaN and infinities replaced by None.

    The pipelines write their JSON with Python's encoder, which emits NaN where a modeled curve
    has no value; browsers reject that, so files go out through this.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def json_file(path: Path) -> JSONResponse:
    """A run file as a browser-safe JSON response."""
    return JSONResponse(json_safe(json.loads(path.read_text(encoding="utf-8"))))


def follow_ridge(
    t: np.ndarray,
    f: np.ndarray,
    bright: np.ndarray,
    allowed: np.ndarray,
    max_step: float,
    max_gap_s: float,
) -> np.ndarray:
    """Frames that belong to the one line through the brightest allowed frame.

    Starting at the brightest allowed frame, walk outward in both directions, accepting a frame
    when it is allowed and its frequency is within `max_step` (a fraction) of the last accepted
    one, and stopping once the line has been missing for more than `max_gap_s`.
    """
    out = np.zeros(t.size, dtype=bool)
    candidates = np.flatnonzero(allowed)
    seed = int(candidates[np.argmax(bright[candidates])])
    out[seed] = True
    for direction in (1, -1):
        last_f = float(f[seed])
        last_t = float(t[seed])
        i = seed + direction
        while 0 <= i < t.size:
            if abs(float(t[i]) - last_t) > max_gap_s:
                break
            if allowed[i] and abs(float(f[i]) - last_f) <= max_step * last_f:
                out[i] = True
                last_f = float(f[i])
                last_t = float(t[i])
            i += direction
    return out


def require_local_token(request: Request) -> None:
    """Reject requests from other hosts and requests without the session token."""
    host = request.headers.get("host", "").rsplit(":", 1)[0]
    if host not in LOCAL_HOSTS:
        raise HTTPException(403, "the UI server answers local requests only")
    ws: Workspace = request.app.state.workspace
    token = request.headers.get("x-earwitness-token") or request.query_params.get("token")
    if token != ws.token:
        raise HTTPException(401, "missing or wrong session token")


def workspace(request: Request) -> Workspace:
    """The app's workspace."""
    return request.app.state.workspace


Ws = Annotated[Workspace, Depends(workspace)]
"""Route parameter type for the workspace; module level so string annotations resolve."""


def create_app(ws: Workspace) -> FastAPI:
    """Build the FastAPI app around a workspace."""
    app = FastAPI(title="Earwitness", version=__version__, docs_url=None, redoc_url=None)
    app.state.workspace = ws
    api = APIRouter(prefix="/api", dependencies=[Depends(require_local_token)])

    def recording(ws: Workspace, rid: str) -> object:
        try:
            return ws.recording(rid)
        except KeyError as exc:
            raise HTTPException(404, f"recording {rid} is not open") from exc

    def run_folder(ws: Workspace, run_id: str) -> Path:
        try:
            return ws.run_dir(run_id)
        except KeyError as exc:
            raise HTTPException(404, f"no run {run_id}") from exc

    def job_or_404(ws: Workspace, job_id: str) -> object:
        job = ws.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"no job {job_id}")
        return job

    # ------------------------------------------------------------------ health and settings

    @api.get("/health")
    def health(ws: Ws) -> dict[str, object]:
        return {
            "version": __version__,
            "ffmpeg": ffmpeg_path() is not None,
            "cache_root": str(ws.cache_root),
            "runs_dir": str(ws.runs_dir.resolve()),
            "ui_built": (STATIC_DIR / "index.html").is_file(),
            "busy": ws.jobs.busy,
            "recordings": len(ws.recordings),
        }

    # ------------------------------------------------------------------ recordings and views

    @api.post("/recordings/open")
    def open_recording(req: OpenRequest, ws: Ws) -> dict[str, object]:
        try:
            rec = ws.open_recording(req.path)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"no such file: {exc}") from exc
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(400, f"could not decode the file: {exc}") from exc
        return rec.info()

    @api.get("/recordings")
    def list_recordings(ws: Ws) -> list[dict[str, object]]:
        return [r.info() for r in ws.recordings.values()]

    @api.get("/recordings/{rid}")
    def recording_info(rid: str, ws: Ws) -> dict[str, object]:
        return recording(ws, rid).info()  # type: ignore[attr-defined]

    @api.get("/recordings/{rid}/spectrogram")
    def recording_spectrogram(
        rid: str,
        ws: Ws,
        t_lo: Annotated[float | None, Query()] = None,
        t_hi: Annotated[float | None, Query()] = None,
        f_lo: Annotated[float | None, Query()] = None,
        f_hi: Annotated[float | None, Query()] = None,
        nfft: Annotated[int, Query(ge=256, le=32768)] = 2048,
        hop: Annotated[int, Query(ge=32)] = 512,
        fmax: Annotated[float, Query(gt=0)] = 600.0,
        max_cols: Annotated[int, Query(ge=50, le=4000)] = 1200,
        max_rows: Annotated[int, Query(ge=20, le=1000)] = 300,
    ) -> dict[str, object]:
        rec = recording(ws, rid)
        fmax = min(fmax, rec.rate / 2.0)  # type: ignore[attr-defined]
        spec = rec.spectrogram(nfft=nfft, hop=hop, fmax=fmax)  # type: ignore[attr-defined]
        return spectrogram_view(spec, t_lo, t_hi, f_lo, f_hi, max_cols, max_rows)

    @api.get("/recordings/{rid}/envelope")
    def recording_envelope(rid: str, ws: Ws) -> dict[str, object]:
        rec = recording(ws, rid)
        return envelope_view(rec.envelope(), rec.wind())  # type: ignore[attr-defined]

    @api.post("/recordings/{rid}/ridge")
    def recording_ridge(rid: str, req: RidgeRequest, ws: Ws) -> dict[str, object]:
        from earwitness.audio.spectrogram import ridge

        rec = recording(ws, rid)
        fmax = max(req.f_hi * 1.05, 50.0)
        spec = rec.spectrogram(nfft=req.nfft, hop=req.hop, fmax=fmax)  # type: ignore[attr-defined]
        try:
            t, f = ridge(spec, req.f_lo, req.f_hi, whitened=req.whitened)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        rows = (spec.f >= req.f_lo) & (spec.f <= req.f_hi)
        view = spec.whitened_db if req.whitened else 10.0 * np.log10(spec.power + 1e-30)
        bright = view[rows].max(axis=0)
        keep = bright >= req.min_db
        if req.t_lo is not None:
            keep &= t >= req.t_lo
        if req.t_hi is not None:
            keep &= t <= req.t_hi
        if req.follow and keep.any():
            dt = float(t[1] - t[0]) if t.size > 1 else 1.0
            width = max(1, round(2.0 / dt))
            smooth = np.convolve(bright, np.ones(width) / width, mode="same")
            window = keep if req.t_lo is None and req.t_hi is None else keep | (bright < req.min_db)
            typical = float(np.median(smooth[window])) if window.any() else 0.0
            keep &= smooth >= typical + req.min_margin_db
            if keep.any():
                keep = follow_ridge(t, f, bright, keep, req.max_step, req.max_gap_s)
        t, f, bright = t[keep], f[keep], bright[keep]
        return {
            "t": np.round(t, 3).tolist(),
            "f": np.round(f, 3).tolist(),
            "db": np.round(bright, 1).tolist(),
        }

    @api.post("/recordings/{rid}/audit")
    def recording_audit(rid: str, ws: Ws) -> dict[str, object]:
        from earwitness.audit.report import audit_file

        rec = recording(ws, rid)
        return audit_file(rec.path, rec.samples, rec.rate).to_dict()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ Doppler fit tools

    @api.post("/doppler/preview")
    def doppler_preview(req: PreviewRequest) -> dict[str, object]:
        from earwitness.aircraft.fit import preview_curve

        if req.t_hi <= req.t_lo or req.step_s <= 0:
            raise HTTPException(400, "the time window must be increasing")
        n = int((req.t_hi - req.t_lo) / req.step_s) + 1
        if n > 20000:
            raise HTTPException(400, "too many samples; widen the step")
        t = req.t_lo + req.step_s * np.arange(n)
        try:
            f = preview_curve(t, req.f0, req.v, req.d, req.t0)
        except (ValueError, FloatingPointError) as exc:
            raise HTTPException(400, str(exc)) from exc
        harmonics = max(1, min(req.harmonics, 12))
        return {
            "t": np.round(t, 3).tolist(),
            "curves": [np.round(f * n, 3).tolist() for n in range(1, harmonics + 1)],
        }

    @api.post("/doppler/fit")
    def doppler_fit(req: FitRequest) -> dict[str, object]:
        from earwitness.aircraft.fit import fit_straight_flyover

        pts = np.asarray(req.points, dtype=float)
        if pts.ndim != 2 or pts.shape[1] != 2:
            raise HTTPException(400, "points must be [[t_s, f_hz], ...]")
        try:
            return fit_straight_flyover(pts[:, 0], pts[:, 1]).to_dict()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------------------ data and estimates

    @api.get("/data/status")
    def data_status(
        ws: Ws,
        date: str,
        bbox: Annotated[list[float], Query(min_length=4, max_length=4)],
        variant: str = "prod",
    ) -> dict[str, object]:
        try:
            return runs.data_status(ws, date, bbox, variant)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api.post("/data/adsb", status_code=202)
    def data_adsb(req: runs.AdsbFetchRequest, ws: Ws) -> dict[str, object]:
        job = ws.jobs.submit(
            "data-adsb", f"adsb.lol {req.date}", lambda p: runs.fetch_adsb(ws, req, p)
        )
        return job.snapshot()

    @api.post("/estimate")
    def estimate(req: runs.EstimateRequest) -> dict[str, object]:
        try:
            return runs.estimate(req)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------------------ runs and jobs

    @api.post("/runs/thunder", status_code=202)
    def start_thunder(req: runs.ThunderRunRequest, ws: Ws) -> dict[str, object]:
        rec = recording(ws, req.recording_id)
        job = ws.jobs.submit(
            "thunder",
            f"thunder: {rec.path.name}",  # type: ignore[attr-defined]
            lambda p: runs.run_thunder(ws, req, p),
        )
        return job.snapshot()

    @api.post("/runs/aircraft", status_code=202)
    def start_aircraft(req: runs.AircraftRunRequest, ws: Ws) -> dict[str, object]:
        rec = recording(ws, req.recording_id)
        job = ws.jobs.submit(
            "aircraft",
            f"aircraft: {rec.path.name}",  # type: ignore[attr-defined]
            lambda p: runs.run_aircraft(ws, req, p),
        )
        return job.snapshot()

    @api.get("/jobs")
    def list_jobs(ws: Ws) -> list[dict[str, object]]:
        out = []
        for job in ws.jobs.jobs():
            snap = job.snapshot()
            snap["lines"] = []
            out.append(snap)
        return out

    @api.get("/jobs/{job_id}")
    def job_status(
        job_id: str, ws: Ws, since: Annotated[int, Query(ge=0)] = 0
    ) -> dict[str, object]:
        return job_or_404(ws, job_id).snapshot(since)  # type: ignore[attr-defined]

    @api.get("/runs")
    def list_runs(ws: Ws) -> list[dict[str, object]]:
        return ws.list_runs()

    @api.get("/runs/{run_id}")
    def run_summary(run_id: str, ws: Ws) -> JSONResponse:
        return json_file(run_folder(ws, run_id) / "summary.json")

    @api.get("/runs/{run_id}/heatmap")
    def run_heatmap(run_id: str, ws: Ws) -> FileResponse:
        path = run_folder(ws, run_id) / "heatmap.geojson"
        if not path.is_file():
            raise HTTPException(404, "this run has no heat map")
        return FileResponse(path, media_type="application/geo+json")

    @api.get("/runs/{run_id}/overlay/{rank}")
    def run_overlay(run_id: str, rank: Annotated[int, PathParam(ge=1)], ws: Ws) -> JSONResponse:
        path = run_folder(ws, run_id) / "overlays" / f"rank_{rank:02d}.json"
        if not path.is_file():
            raise HTTPException(404, f"no overlay for rank {rank}")
        return json_file(path)

    @api.get("/runs/{run_id}/histogram")
    def run_histogram(
        run_id: str, ws: Ws, bins: Annotated[int, Query(ge=5, le=200)] = 50
    ) -> dict[str, object]:
        folder = run_folder(ws, run_id)
        table = folder / "results.parquet"
        if table.is_file():
            import pyarrow.parquet as pq

            scores = pq.read_table(table, columns=["score"]).column("score").to_numpy()
            scores = scores[np.isfinite(scores)]
            if scores.size:
                counts, edges = np.histogram(scores, bins=bins)
                return {
                    "edges": np.round(edges, 4).tolist(),
                    "counts": counts.tolist(),
                    "n": int(scores.size),
                }
        hist = folder / "histogram.json"
        if hist.is_file():
            return json.loads(hist.read_text(encoding="utf-8"))
        raise HTTPException(404, "this run has no scores to histogram")

    @api.get("/runs/{run_id}/log")
    def run_log(run_id: str, ws: Ws) -> PlainTextResponse:
        path = run_folder(ws, run_id) / "run.log"
        if not path.is_file():
            raise HTTPException(404, "this run has no log")
        return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"))

    @api.get("/runs/{run_id}/events")
    def run_events(run_id: str, ws: Ws) -> JSONResponse:
        path = run_folder(ws, run_id) / "events.json"
        if not path.is_file():
            raise HTTPException(404, "this run has no events file")
        return json_file(path)

    app.include_router(api)

    # ------------------------------------------------------------------ the front end

    index = STATIC_DIR / "index.html"
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def root() -> Response:
        if index.is_file():
            return FileResponse(index, media_type="text/html")
        return HTMLResponse(MISSING_UI, status_code=200)

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "no such route")
        candidate = STATIC_DIR / path
        if path and candidate.is_file() and STATIC_DIR in candidate.resolve().parents:
            return FileResponse(candidate)
        return root()

    return app
