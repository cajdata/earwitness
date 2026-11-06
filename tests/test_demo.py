"""`earwitness demo`, the install check: both synthetic cases are located, offline."""

import json

from earwitness import cli, demo
from earwitness.demo import DEMO_LAT, DEMO_LON, DemoResult, result_line, run_aircraft_demo


def test_cli_demo_locates_both_cases(tmp_path, capsys):
    out, runs = tmp_path / "demo", tmp_path / "runs"
    assert cli.main(["demo", "--out", str(out), "--runs-dir", str(runs)]) == 0
    text = capsys.readouterr().out
    assert text.count("PASS") == 2
    assert "demo passed" in text
    for method in ("thunder", "aircraft"):
        assert (out / method / "recording.wav").is_file()
        assert (out / method / "truth.json").is_file()
        summary = json.loads((runs / f"demo-{method}" / "summary.json").read_text())
        assert summary["top"][0]["rank"] == 1
    assert (out / "aircraft" / "events.json").is_file()
    truth = json.loads((out / "thunder" / "truth.json").read_text())
    assert (truth["observer"]["lat"], truth["observer"]["lon"]) == (DEMO_LAT, DEMO_LON)


def test_demo_scores_aircraft_candidates_in_child_processes(tmp_path):
    lines: list[str] = []
    result = run_aircraft_demo(tmp_path / "demo", tmp_path / "runs", lines.append, workers=2)
    assert result.passed
    assert result.error_m < 200.0
    assert abs(result.start_error_s) < 2.0
    log = (result.run_dir / "run.log").read_text()
    assert "on 2 processes" in log
    # a second run gets a fresh folder instead of writing over the first
    again = run_aircraft_demo(tmp_path / "demo", tmp_path / "runs", lines.append)
    assert again.run_dir.name == "demo-aircraft-2"
    assert abs(again.error_m - result.error_m) < 1e-6  # same scores serial and in the pool


def test_result_line_reads_pass_and_fail(tmp_path):
    ok = DemoResult("thunder", 242.0, 1.0, 5000.0, 1, 0.99, 1.2, tmp_path, tmp_path)
    assert result_line(ok).startswith("thunder   rank 1 is 242 m from the true site, start +1.0 s")
    assert "PASS (needs rank 1 within 5 km)" in result_line(ok)
    far = DemoResult("aircraft", 1530.0, -0.2, 200.0, None, 20.0, 5.0, tmp_path, tmp_path)
    assert "1.53 km" in result_line(far)
    assert "FAIL (needs rank 1 within 200 m)" in result_line(far)


def test_install_checks_pass_here_and_report_failures(monkeypatch):
    checks = {c.label: c for c in demo.install_checks()}
    for label in (
        "terrain tiles (rasterio)",
        "lightning files (h5netcdf)",
        "data cache (Parquet)",
        "land mask (shapely)",
    ):
        assert checks[label].status == "ok", checks[label]
    assert checks["terrain tiles (rasterio)"].detail.startswith("GDAL ")
    assert checks["desktop window"].status in ("ok", "info")
    assert checks["video and other audio"].status in ("ok", "info")

    def broken() -> str:
        raise ModuleNotFoundError("No module named 'rasterio.serde'")

    monkeypatch.setattr(demo, "_check_terrain", broken)
    failed = {c.label: c for c in demo.install_checks()}["terrain tiles (rasterio)"]
    assert failed.status == "fail"
    assert "rasterio.serde" in failed.detail
    assert demo.check_line(failed).startswith("install   terrain tiles (rasterio)")
    assert " FAIL " in demo.check_line(failed)


def test_cli_demo_fails_when_an_install_check_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        demo, "install_checks", lambda: [demo.Check("terrain tiles (rasterio)", "fail", "boom")]
    )
    args = ["demo", "--out", str(tmp_path / "d"), "--runs-dir", str(tmp_path / "r")]
    assert cli.main(args) == 1
    assert "an install check failed" in capsys.readouterr().out
