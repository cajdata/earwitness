"""The UI's API: open a recording, serve its views, run both methods and read the results back."""

import json
import urllib.request
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

import earwitness.data.glm as glm
from earwitness.aircraft.pipeline import distance_to as aircraft_distance
from earwitness.aircraft.prep import flights_from_tracks_json
from earwitness.audio.decode import write_wav
from earwitness.audio.envelope import Envelope
from earwitness.audio.events import events_from_truth
from earwitness.audio.spectrogram import Spectrogram
from earwitness.core.grid import BBox
from earwitness.core.physics import straight_line_doppler
from earwitness.data.adapters import load_events
from earwitness.server import runs
from earwitness.server.app import create_app
from earwitness.server.launch import ServerThread, free_port
from earwitness.server.workspace import Workspace, block_reduce, envelope_view, spectrogram_view
from earwitness.simulate import MixOptions, write_aircraft_case, write_thunder_case
from earwitness.simulate.aircraft_synth import AircraftScenario, Flyover
from earwitness.simulate.thunder_synth import ThunderScenario, random_storm
from earwitness.thunder.pipeline import distance_to as thunder_distance

LAT, LON = 35.0, -97.0
RATE = 4000
THUNDER_START = datetime(2026, 7, 2, 21, 0, tzinfo=UTC)
AIRCRAFT_START = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)


@pytest.fixture
def api(tmp_path):
    ws = Workspace(cache=tmp_path / "cache", runs_dir=tmp_path / "runs")
    client = TestClient(
        create_app(ws), base_url="http://127.0.0.1", headers={"X-Earwitness-Token": ws.token}
    )
    return client, ws


def _tone_wav(path, seconds=60.0, f=120.0, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    x = 0.05 * rng.standard_normal(t.size) + 0.2 * np.sin(2 * np.pi * f * t)
    return write_wav(path, x, RATE)


def _thunder_case(tmp_path, seed=11):
    rng = np.random.default_rng(seed)
    strikes = []
    for bearing, distance, n in [(50.0, 9000.0, 18), (200.0, 12000.0, 18)]:
        strikes += random_storm(
            rng,
            LAT,
            LON,
            900.0,
            n,
            center_bearing_deg=bearing,
            center_distance_m=distance,
            radius_m=4000.0,
            drift_speed_mps=8.0,
            drift_bearing_deg=bearing + 60.0,
        )
    strikes.sort(key=lambda s: s.time_s)
    scenario = ThunderScenario(LAT, LON, 0.0, THUNDER_START, 900.0, strikes)
    case = tmp_path / "thunder-case"
    write_thunder_case(case, scenario, RATE, MixOptions(snr_db=10.0), seed, rng)
    return case


def _aircraft_case(tmp_path, seed=21):
    flyovers = [
        Flyover(70.0, 400.0, 90.0, 300.0, t_cpa_s=300.0, f0_hz=110.0, harmonics=6, icao="ad5b01"),
        Flyover(85.0, 500.0, 200.0, -250.0, t_cpa_s=540.0, f0_hz=95.0, harmonics=6, icao="ad5b02"),
    ]
    scenario = AircraftScenario(
        LAT, LON, 0.0, AIRCRAFT_START, 900.0, flyovers, track_half_span_s=400.0
    )
    case = tmp_path / "aircraft-case"
    truth = write_aircraft_case(
        case, scenario, RATE, MixOptions(snr_db=-15.0), seed, np.random.default_rng(seed)
    )
    flights = flights_from_tracks_json(case / "tracks.json")
    decoys = flights_from_tracks_json(case / "tracks.json")
    for d, (dt, dlat) in zip(decoys, [(2000.0, 0.08), (-1500.0, -0.005)], strict=True):
        d.icao = "decoy" + d.icao[-1]
        d.time_unix = d.time_unix + dt
        d.lat = d.lat + dlat
    return case, truth, flights + decoys


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- access control


def test_api_needs_the_session_token_and_a_local_host(api):
    client, ws = api
    assert client.get("/api/health", headers={"X-Earwitness-Token": "wrong"}).status_code == 401
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    ok = client.get("/api/health")
    assert ok.status_code == 200
    assert ok.json()["version"]
    assert ok.json()["recordings"] == 0
    # the launcher puts the token in the page URL; the query string works too
    by_query = client.get(f"/api/health?token={ws.token}", headers={"X-Earwitness-Token": ""})
    assert by_query.status_code == 200
    # the page itself needs no token
    assert client.get("/").status_code == 200
    assert client.get("/api/nope").status_code in (401, 404)


# ----------------------------------------------------------------------------- recordings


def test_open_recording_and_its_views(api, tmp_path):
    client, _ = api
    wav = _tone_wav(tmp_path / "tone.wav")
    opened = client.post("/api/recordings/open", json={"path": str(wav)})
    assert opened.status_code == 200
    info = opened.json()
    rid = info["id"]
    assert abs(info["duration_s"] - 60.0) < 1e-6
    assert info["name"] == "tone.wav"
    assert client.post("/api/recordings/open", json={"path": str(wav)}).json()["id"] == rid
    assert client.get("/api/recordings").json()[0]["id"] == rid
    assert client.get(f"/api/recordings/{rid}").json()["sha256"] == info["sha256"]

    spec = client.get(
        f"/api/recordings/{rid}/spectrogram", params={"f_hi": 300, "max_cols": 100, "max_rows": 40}
    ).json()
    assert 0 < len(spec["t"]) <= 100
    assert 0 < len(spec["f"]) <= 40
    assert len(spec["db"]) == len(spec["f"])
    assert len(spec["db"][0]) == len(spec["t"])
    assert max(spec["f"]) <= 300.0
    # the tone row is the brightest one
    brightest = int(np.argmax(np.max(np.array(spec["db"]), axis=1)))
    assert abs(spec["f"][brightest] - 120.0) < 2 * spec["f_step_hz"]

    env = client.get(f"/api/recordings/{rid}/envelope").json()
    assert len(env["t"]) == len(env["values"]) > 10
    assert len(env["wind"]) == len(env["t"])

    ridge = client.post(
        f"/api/recordings/{rid}/ridge",
        json={"f_lo": 100, "f_hi": 140, "min_db": -200, "whitened": False},
    ).json()
    assert abs(float(np.median(ridge["f"])) - 120.0) < 2.0
    assert len(ridge["t"]) == len(ridge["f"]) == len(ridge["db"])

    audit = client.post(f"/api/recordings/{rid}/audit").json()
    assert [c["kind"] for c in audit["clues"]] == ["metadata", "thunder", "aircraft", "mains_hum"]
    assert client.get("/api/recordings/nope").status_code == 404


def test_open_missing_file_is_404(api, tmp_path):
    client, _ = api
    r = client.post("/api/recordings/open", json={"path": str(tmp_path / "missing.wav")})
    assert r.status_code == 404


# ----------------------------------------------------------------------------- fit tools


def test_doppler_preview_and_fit(api):
    client, _ = api
    preview = client.post(
        "/api/doppler/preview",
        json={"f0": 110, "v": 70, "d": 400, "t0": 300, "t_lo": 240, "t_hi": 360, "step_s": 0.5,
              "harmonics": 2},
    ).json()  # fmt: skip
    assert len(preview["curves"]) == 2
    assert len(preview["curves"][0]) == len(preview["t"]) == 241
    assert preview["curves"][0][0] > 110.0 > preview["curves"][0][-1]
    assert abs(preview["curves"][1][0] - 2 * preview["curves"][0][0]) < 1e-6

    t = np.arange(250.0, 350.0, 0.5)
    f = straight_line_doppler(t, 110.0, 70.0, 400.0, 300.0).f
    fit = client.post("/api/doppler/fit", json={"points": np.column_stack([t, f]).tolist()}).json()
    assert abs(fit["f0_hz"] - 110.0) < 0.2
    assert abs(fit["v_mps"] - 70.0) < 1.0
    assert abs(fit["d_m"] - 400.0) < 10.0
    assert fit["ranges"]["v_mps"][0] <= 70.0 <= fit["ranges"]["v_mps"][1]
    assert client.post("/api/doppler/fit", json={"points": [[0, 1], [1, 2]]}).status_code == 422
    bad = client.post(
        "/api/doppler/preview",
        json={"f0": 110, "v": 70, "d": 400, "t0": 300, "t_lo": 10, "t_hi": 5},
    )
    assert bad.status_code == 400


def test_estimate_describes_the_search_size(api):
    client, _ = api
    bbox = [-100.0, 30.0, -85.0, 42.0]
    thunder = client.post(
        "/api/estimate", json={"method": "thunder", "date": "2026-07-02", "bbox": bbox}
    ).json()
    assert thunder["coarse_cells"] > 1000
    assert thunder["start_times"] == 17280
    assert "about" in thunder["text"]
    two = client.post(
        "/api/estimate", json={"method": "aircraft", "date": "2026-09-08", "bbox": bbox}
    ).json()
    one = client.post(
        "/api/estimate",
        json={"method": "aircraft", "date": "2026-09-08", "bbox": bbox, "known_time": True},
    ).json()
    assert two["seconds"] > one["seconds"] > 0
    assert "single-event" in one["text"]


def test_data_status_on_an_empty_cache(api):
    client, _ = api
    r = client.get(
        "/api/data/status", params={"date": "2026-09-08", "bbox": [-98.0, 34.0, -96.0, 36.0]}
    )
    assert r.status_code == 200
    status = r.json()
    assert status["adsb"]["covered"] is False
    assert status["adsb"]["regions"] == []
    assert status["glm"]["hours_cached"] == 0
    assert status["glm"]["satellite"]


# ----------------------------------------------------------------------------- runs


def test_thunder_run_through_the_api(api, tmp_path, monkeypatch):
    client, ws = api
    case = _thunder_case(tmp_path)
    events = load_events(case / "strikes.json")
    monkeypatch.setattr(glm, "load_flashes", lambda *args, **kwargs: events)
    rid = client.post("/api/recordings/open", json={"path": str(case / "recording.wav")}).json()[
        "id"
    ]
    request = {
        "recording_id": rid,
        "date": "2026-07-02",
        "time_window": [
            _iso(THUNDER_START - timedelta(minutes=30)),
            _iso(THUNDER_START + timedelta(minutes=30)),
        ],
        "bbox": list(BBox.around(LAT, LON, 60000.0).as_tuple()),
        "land_mask": False,
        "top": 5,
    }
    started = client.post("/api/runs/thunder", json=request)
    assert started.status_code == 202
    job = ws.jobs.wait(started.json()["id"], timeout=600)
    assert job.status == "done", job.error
    snap = client.get(f"/api/jobs/{job.id}").json()
    assert snap["status"] == "done"
    assert snap["lines"]
    assert snap["next"] == len(snap["lines"])
    assert client.get(f"/api/jobs/{job.id}", params={"since": snap["next"]}).json()["lines"] == []
    assert client.get("/api/jobs").json()[0]["id"] == job.id

    run_id = job.result["run_id"]
    cards = client.get("/api/runs").json()
    assert cards[0]["id"] == run_id
    assert cards[0]["method"] == "thunder"
    summary = client.get(f"/api/runs/{run_id}").json()
    assert summary["schema"] == "earwitness.thunder-run/1"
    assert thunder_distance(summary, LAT, LON)[0] < 500.0
    heat = client.get(f"/api/runs/{run_id}/heatmap").json()
    assert heat["type"] == "FeatureCollection"
    hist = client.get(f"/api/runs/{run_id}/histogram").json()
    assert sum(hist["counts"]) == hist["n"] > 0
    overlay = client.get(f"/api/runs/{run_id}/overlay/1").json()
    assert len(overlay["observed"]) == len(overlay["modeled"]) == len(overlay["t_s"])
    assert "done in" in client.get(f"/api/runs/{run_id}/log").text
    assert client.get(f"/api/runs/{run_id}/events").status_code == 404
    assert client.get(f"/api/runs/{run_id}/overlay/99").status_code == 404
    assert client.get("/api/runs/..").status_code == 404
    assert client.get("/api/runs/does-not-exist").status_code == 404


def test_aircraft_run_through_the_api(api, tmp_path, monkeypatch):
    client, ws = api
    case, truth, flights = _aircraft_case(tmp_path)
    monkeypatch.setattr(
        runs, "load_cached_flights", lambda ws, day, region, variant, progress: flights
    )
    events = events_from_truth(truth).to_dict()
    assert len(events["events"]) == 2
    rid = client.post("/api/recordings/open", json={"path": str(case / "recording.wav")}).json()[
        "id"
    ]
    request = {
        "recording_id": rid,
        "date": "2026-09-08",
        "time_window": [
            _iso(AIRCRAFT_START - timedelta(minutes=10)),
            _iso(AIRCRAFT_START + timedelta(minutes=10)),
        ],
        "bbox": list(BBox.around(LAT, LON, 15000.0).as_tuple()),
        "events": events,
        "terrain": False,
        "top": 5,
    }
    started = client.post("/api/runs/aircraft", json=request)
    assert started.status_code == 202
    job = ws.jobs.wait(started.json()["id"], timeout=900)
    assert job.status == "done", job.error
    run_id = job.result["run_id"]
    summary = client.get(f"/api/runs/{run_id}").json()
    assert summary["schema"] == "earwitness.aircraft-run/1"
    assert aircraft_distance(summary, LAT, LON)[0] < 200.0
    assert [f["icao"] for f in summary["top"][0]["flights"]] == ["ad5b01", "ad5b02"]
    saved = client.get(f"/api/runs/{run_id}/events").json()
    assert saved["schema"] == "earwitness.events/1"
    assert saved["recording"]["file"] == "recording.wav"
    overlay = client.get(f"/api/runs/{run_id}/overlay/1").json()
    assert len(overlay["curves"]) == 2
    assert client.get(f"/api/runs/{run_id}/heatmap").json()["features"]
    assert client.get(f"/api/runs/{run_id}/histogram").json()["n"] > 0
    assert client.get("/api/runs").json()[0]["method"] == "aircraft"


def test_run_with_an_unknown_recording_is_404(api):
    client, _ = api
    r = client.post(
        "/api/runs/thunder",
        json={"recording_id": "nope", "date": "2026-07-02", "bbox": [-98, 34, -96, 36]},
    )
    assert r.status_code == 404


def test_failed_jobs_report_their_error(api, tmp_path, monkeypatch):
    client, ws = api
    wav = _tone_wav(tmp_path / "tone.wav", seconds=20.0)
    rid = client.post("/api/recordings/open", json={"path": str(wav)}).json()["id"]

    def boom(*args, **kwargs):
        raise RuntimeError("no flashes today")

    monkeypatch.setattr(glm, "load_flashes", boom)
    started = client.post(
        "/api/runs/thunder",
        json={"recording_id": rid, "date": "2026-07-02", "bbox": [-98, 34, -96, 36],
              "land_mask": False},
    )  # fmt: skip
    job = ws.jobs.wait(started.json()["id"], timeout=60)
    assert job.status == "failed"
    assert "no flashes today" in job.error
    snap = client.get(f"/api/jobs/{job.id}").json()
    assert snap["status"] == "failed"
    assert any("RuntimeError" in line for line in snap["lines"])


# ----------------------------------------------------------------------------- helpers


def test_block_reduce_takes_block_maxima_and_means():
    values = np.arange(10.0)
    assert block_reduce(values, 4, 0).tolist() == [3.0, 7.0, 9.0]
    assert block_reduce(values, 4, 0, "mean").tolist() == [1.5, 5.5, 8.5]
    assert block_reduce(values, 1, 0).tolist() == values.tolist()
    grid = np.arange(12.0).reshape(3, 4)
    assert block_reduce(grid, 2, 1).tolist() == [[1.0, 3.0], [5.0, 7.0], [9.0, 11.0]]


def test_spectrogram_and_envelope_views_crop_and_reduce():
    t = np.arange(0.0, 10.0, 0.5)
    f = np.arange(0.0, 100.0, 10.0)
    db = np.outer(np.ones(f.size), np.arange(t.size, dtype=float))
    spec = Spectrogram(t=t, f=f, power=np.ones_like(db), whitened_db=db)
    view = spectrogram_view(spec, t_lo=2.0, t_hi=6.0, f_lo=20.0, f_hi=50.0, max_cols=3)
    assert view["f"] == [20.0, 30.0, 40.0, 50.0]
    assert len(view["t"]) == 3
    assert len(view["db"]) == 4
    assert len(view["db"][0]) == 3
    assert view["db"][0][-1] == 12.0  # block maxima keep the brightest frame
    assert spectrogram_view(spec, t_lo=50.0)["db"] == []

    env = Envelope(t=np.arange(0.0, 8.0, 0.25), values=np.arange(32.0), frame_s=0.25)
    wind = np.zeros(32, dtype=bool)
    wind[4] = True
    ev = envelope_view(env, wind, max_points=8)
    assert len(ev["t"]) == len(ev["values"]) == len(ev["wind"]) == 8
    assert ev["wind"][1] is True
    assert ev["frame_s"] == 1.0
    assert abs(ev["wind_fraction"] - 1 / 32) < 1e-9


def test_run_dir_rejects_paths_outside_the_runs_folder(tmp_path):
    ws = Workspace(runs_dir=tmp_path)
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "summary.json").write_text("{}", encoding="utf-8")
    assert ws.run_dir("good") == tmp_path / "good"
    for bad in ("..", "../good", "a/b", "", "missing"):
        with pytest.raises(KeyError):
            ws.run_dir(bad)


def test_server_thread_serves_the_api_on_localhost():
    ws = Workspace()
    port = free_port()
    server = ServerThread(create_app(ws), "127.0.0.1", port)
    server.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/health", headers={"X-Earwitness-Token": ws.token}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            assert json.loads(resp.read())["version"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as resp:
            assert resp.status == 200
    finally:
        server.stop()
    assert not server.alive


def test_follow_ridge_keeps_one_continuous_line():
    from earwitness.audio.spectrogram import follow_ridge

    rng = np.random.default_rng(4)
    t = np.arange(400) * 0.128
    f = rng.uniform(90.0, 140.0, t.size)
    bright = rng.normal(7.0, 1.5, t.size)
    line = np.arange(100, 200)
    f[line] = 125.0 - 0.3 * (line - 100)  # a falling line, 125 to 95 Hz
    bright[line] = 15.0
    allowed = bright >= 10.0
    allowed[[20, 50, 300]] = True  # bright noise peaks far from the line in frequency
    kept = follow_ridge(t, f, bright, allowed, max_step=0.04, max_gap_s=1.5)
    kept_idx = np.flatnonzero(kept)
    assert kept_idx.min() >= 100
    assert kept_idx.max() <= 199
    assert kept_idx.size >= 95


def test_run_files_with_nan_reach_the_browser_as_null(api, tmp_path):
    client, ws = api
    folder = ws.runs_dir / "nan-run"
    (folder / "overlays").mkdir(parents=True)
    (folder / "summary.json").write_text(
        '{"schema": "earwitness.aircraft-run/1", "top": [], "coarse": NaN}', encoding="utf-8"
    )
    (folder / "overlays" / "rank_01.json").write_text(
        '{"curves": [{"modeled_hz": [1.0, NaN, Infinity]}]}', encoding="utf-8"
    )
    assert client.get("/api/runs/nan-run").json()["coarse"] is None
    overlay = client.get("/api/runs/nan-run/overlay/1").json()
    assert overlay["curves"][0]["modeled_hz"] == [1.0, None, None]
