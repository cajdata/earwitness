"""The console-less launcher and the window fallback: a clean machine must never get a blank
failure when it double-clicks the app."""

import sys
import webbrowser

import pytest

from earwitness import desktop
from earwitness.server import launch as launch_mod


def test_launch_falls_back_to_the_browser_and_waits(monkeypatch, tmp_path):
    opened: list[str] = []
    waited: list[str] = []
    said: list[str] = []
    monkeypatch.setattr(launch_mod, "window_unavailable", lambda: "no window toolkit here")
    monkeypatch.setattr(webbrowser, "open", opened.append)
    code = launch_mod.launch(
        cache=tmp_path / "cache",
        runs_dir=tmp_path / "runs",
        mode="window",
        echo=said.append,
        wait=waited.append,
    )
    assert code == 0
    assert opened == waited
    assert opened[0].startswith("http://127.0.0.1:")
    assert "token=" in opened[0]
    assert any("no window toolkit here; opening the system browser instead" in s for s in said)


def test_launch_falls_back_when_the_window_fails(monkeypatch, tmp_path):
    opened: list[str] = []
    said: list[str] = []

    def broken_window(url: str) -> None:
        raise RuntimeError("GTK is missing")

    monkeypatch.setattr(launch_mod, "window_unavailable", lambda: None)
    monkeypatch.setattr(launch_mod, "_open_window", broken_window)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    code = launch_mod.launch(
        runs_dir=tmp_path / "runs", mode="window", echo=said.append, wait=lambda url: None
    )
    assert code == 0
    assert len(opened) == 1
    assert any("failed to open: GTK is missing" in s for s in said)


def test_window_unavailable_without_pywebview(monkeypatch):
    monkeypatch.setattr(launch_mod.importlib.util, "find_spec", lambda name: None)
    assert launch_mod.window_unavailable() == "pywebview is not installed"


@pytest.mark.skipif(sys.platform != "win32", reason="the WebView2 check reads the Windows registry")
def test_webview2_version_reads_the_newest_channel(monkeypatch):
    import winreg

    class Key:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    values = {
        launch_mod.WEBVIEW2_CLIENT_KEYS[0]: "0.0.0.0",  # uninstalled channel
        launch_mod.WEBVIEW2_CLIENT_KEYS[1]: "131.0.2903.5",
        launch_mod.WEBVIEW2_CLIENT_KEYS[2]: "86.0.600.0",  # older than pywebview accepts
    }

    def open_key(hive, path):
        for client, value in values.items():
            if path.endswith(client):
                return Key(value)
        raise OSError("no such key")

    monkeypatch.setattr(winreg, "OpenKey", open_key)
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (key.value, 1))
    assert launch_mod.webview2_version() == "131.0.2903.5"
    values.clear()
    assert launch_mod.webview2_version() is None
    monkeypatch.setattr(launch_mod.importlib.util, "find_spec", lambda name: object())
    assert "WebView2 Runtime is not installed" in launch_mod.window_unavailable()


def test_desktop_main_without_a_console_logs_and_passes_the_box(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def fake_launch(**kwargs):
        seen.update(kwargs)
        print("server says hello")
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(launch_mod, "launch", fake_launch)
    assert desktop.main(["--runs-dir", "elsewhere", "--browser"]) == 0
    sys.stdout.close()  # the launcher's log stream
    log = (tmp_path / desktop.LOG_NAME).read_text(encoding="utf-8")
    assert "earwitness-ui" in log
    assert "server says hello" in log
    assert seen["mode"] == "browser"
    assert str(seen["runs_dir"]) == "elsewhere"
    assert seen["wait"] is desktop.keep_running_box


def test_desktop_main_reports_a_failure_in_a_box(monkeypatch, tmp_path):
    boxes: list[tuple[str, bool]] = []

    def failing_launch(**kwargs):
        raise RuntimeError("port trouble")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(launch_mod, "launch", failing_launch)
    monkeypatch.setattr(
        desktop, "message_box", lambda text, error=False: boxes.append((text, error))
    )
    assert desktop.main([]) == 1
    sys.stdout.close()  # the launcher's log stream
    assert len(boxes) == 1
    assert boxes[0][1] is True
    assert "port trouble" in boxes[0][0]
    assert str(tmp_path / desktop.LOG_NAME) in boxes[0][0]
    assert "RuntimeError: port trouble" in (tmp_path / desktop.LOG_NAME).read_text(encoding="utf-8")


def test_desktop_main_with_a_console_keeps_ctrl_c(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launch_mod, "launch", lambda **kw: seen.update(kw) or 0)
    assert desktop.main(["--serve"]) == 0
    assert seen["wait"] is None
    assert seen["mode"] == "serve"
    assert not (tmp_path / desktop.LOG_NAME).exists()


def test_window_unavailable_on_linux_without_a_toolkit(monkeypatch):
    found = {"webview"}
    monkeypatch.setattr(launch_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        launch_mod.importlib.util, "find_spec", lambda name: object() if name in found else None
    )
    assert "neither GTK (PyGObject) nor Qt" in launch_mod.window_unavailable()
    found.add("qtpy")
    assert launch_mod.window_unavailable() is None


def test_desktop_main_reports_bad_arguments_in_a_box(monkeypatch, tmp_path):
    boxes: list[tuple[str, bool]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(
        desktop, "message_box", lambda text, error=False: boxes.append((text, error))
    )
    dropped = str(tmp_path / "recording.wav")
    assert desktop.main([dropped]) == 2
    sys.stdout.close()  # the launcher's log stream
    assert len(boxes) == 1
    assert boxes[0][1] is True
    assert dropped in boxes[0][0]
    assert "unrecognized arguments" in (tmp_path / desktop.LOG_NAME).read_text(encoding="utf-8")
