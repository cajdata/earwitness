"""Start the API server on localhost and open the UI in a desktop window or the browser.

`earwitness ui` runs uvicorn on a worker thread, bound to 127.0.0.1 on a free port, then opens
the page in a pywebview window (Edge WebView2 on Windows, GTK or Qt on Linux). The window gets a
small JavaScript API for native file and folder dialogs, which is the one thing a browser page
cannot do for a local-files-only tool. Without pywebview, with `--browser`, or when the window
cannot open (no WebView2 runtime on Windows, no GTK or Qt on Linux), the system browser opens
the same URL instead and the process waits for Ctrl+C, or for the caller's `wait`.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

import uvicorn

from earwitness.server.app import create_app
from earwitness.server.workspace import Workspace

HOST = "127.0.0.1"
MEDIA_TYPES = (
    "Audio and video (*.wav;*.flac;*.ogg;*.mp3;*.m4a;*.aac;*.mp4;*.mov;*.mkv;*.webm)",
    "All files (*.*)",
)


def free_port(host: str = HOST) -> int:
    """A port the OS currently has free on this host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


class ServerThread:
    """uvicorn on a daemon thread, with a ready check and a clean stop."""

    def __init__(self, app: object, host: str, port: int) -> None:
        self.config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, name="earwitness-http", daemon=True)

    def start(self, timeout: float = 15.0) -> None:
        """Start serving and wait until the socket is open."""
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive():
                raise RuntimeError("the UI server stopped before it was ready")
            if time.monotonic() > deadline:
                raise TimeoutError("the UI server did not start in time")
            time.sleep(0.02)

    def stop(self, timeout: float = 5.0) -> None:
        """Ask uvicorn to exit and wait for the thread."""
        self.server.should_exit = True
        self.thread.join(timeout)

    @property
    def alive(self) -> bool:
        """True while the server thread runs."""
        return self.thread.is_alive()


class DialogApi:
    """Native dialogs for the page, reachable as `window.pywebview.api.*` in JavaScript."""

    def __init__(self) -> None:
        self.window: object | None = None

    def _dialog(self, kind: str) -> str | None:
        import webview

        if self.window is None:
            return None
        dialogs = getattr(webview, "FileDialog", None)
        if dialogs is not None:
            mode = dialogs.FOLDER if kind == "folder" else dialogs.OPEN
        else:  # pywebview 4 constants
            mode = webview.FOLDER_DIALOG if kind == "folder" else webview.OPEN_DIALOG
        kwargs: dict[str, object] = {"allow_multiple": False}
        if kind == "file":
            kwargs["file_types"] = MEDIA_TYPES
        result = self.window.create_file_dialog(mode, **kwargs)  # type: ignore[attr-defined]
        if not result:
            return None
        return str(result[0]) if isinstance(result, (list, tuple)) else str(result)

    def pick_file(self) -> str | None:
        """Open-file dialog; the chosen path or None."""
        return self._dialog("file")

    def pick_folder(self) -> str | None:
        """Choose-folder dialog; the chosen path or None."""
        return self._dialog("folder")


def _say(message: str) -> None:
    """Print and flush, so the URL shows up at once even when stdout is a pipe."""
    print(message, flush=True)


WEBVIEW2_CLIENT_KEYS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # canary
)
"""EdgeUpdate client ids of the WebView2 channels, the registry entries pywebview checks."""
WEBVIEW2_MINIMUM = (86, 0, 622, 0)
"""The oldest WebView2 build pywebview accepts."""


def webview2_version() -> str | None:
    """The newest installed Edge WebView2 build on Windows, or None when there is none.

    pywebview quietly falls back to the retired Internet Explorer engine without WebView2,
    which cannot run this front end, so the launcher checks first and opens the browser.
    """
    if sys.platform != "win32":
        return None
    import winreg

    builds: list[tuple[int, ...]] = []
    for client in WEBVIEW2_CLIENT_KEYS:
        for hive, prefix in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node"),
            (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE"),
            (winreg.HKEY_CURRENT_USER, "SOFTWARE"),
        ):
            try:
                key_path = rf"{prefix}\Microsoft\EdgeUpdate\Clients\{client}"
                with winreg.OpenKey(hive, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, "pv")
            except OSError:
                continue
            parts = tuple(int(p) for p in str(value).split(".") if p.isdigit())
            if parts >= WEBVIEW2_MINIMUM:  # an uninstalled channel can leave pv = 0.0.0.0
                builds.append(parts)
    return ".".join(str(p) for p in max(builds)) if builds else None


LINUX_TOOLKITS = ("gi", "qtpy")
"""Modules pywebview draws with on Linux: PyGObject for GTK, QtPy for Qt (`pywebview[qt]`)."""


def window_unavailable() -> str | None:
    """Why a desktop window cannot open here, or None when it can be tried."""
    if importlib.util.find_spec("webview") is None:
        return "pywebview is not installed"
    if sys.platform == "win32" and webview2_version() is None:
        return "the Microsoft Edge WebView2 Runtime is not installed"
    if sys.platform.startswith("linux") and not any(
        importlib.util.find_spec(name) for name in LINUX_TOOLKITS
    ):
        return "pywebview has neither GTK (PyGObject) nor Qt to draw with"
    return None


def launch(
    cache: str | Path | None = None,
    runs_dir: str | Path | None = None,
    port: int | None = None,
    mode: str = "window",
    echo: Callable[[str], None] = _say,
    wait: Callable[[str], None] | None = None,
) -> int:
    """Serve the UI and open it; returns when the window closes or the user interrupts.

    `mode` is "window" (pywebview, falling back to the browser when no window can open),
    "browser" (system browser) or "serve" (no page opened; for front-end development against
    the Vite dev server, which proxies /api here). Without a window the server runs until
    Ctrl+C, or until `wait(url)` returns when it is given (the console-less launcher passes a
    message box).
    """
    ws = Workspace(cache, runs_dir)
    app = create_app(ws)
    port = port or free_port()
    url = f"http://{HOST}:{port}/?token={ws.token}"
    server = ServerThread(app, HOST, port)
    server.start()
    say = echo
    say(f"Earwitness UI: {url}")
    say(f"runs folder: {ws.runs_dir.resolve()}; cache: {ws.cache_root}")
    try:
        if mode == "window":
            reason = window_unavailable()
            if reason is None:
                try:
                    _open_window(url)
                    return 0
                except Exception as exc:  # no GUI toolkit (GTK or Qt), or the window failed
                    reason = f"the desktop window failed to open: {exc}"
            say(f"{reason}; opening the system browser instead")
            mode = "browser"
        if mode == "browser":
            webbrowser.open(url)
        if wait is not None:
            wait(url)
            return 0
        say("Press Ctrl+C to stop the server.")
        try:
            while server.alive:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        server.stop()


def user_agent() -> str:
    """The window's User-Agent: the OpenStreetMap tile policy asks apps to name themselves."""
    from earwitness import __version__

    return f"Earwitness/{__version__} (+https://earwitness.dev)"


def _open_window(url: str) -> None:
    """The page in a pywebview window, with native file dialogs; returns when it closes."""
    import webview

    api = DialogApi()
    window = webview.create_window(
        "Earwitness", url, width=1440, height=920, min_size=(960, 640), js_api=api
    )
    api.window = window
    webview.start(user_agent=user_agent())
