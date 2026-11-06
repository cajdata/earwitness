"""`earwitness-ui`: the desktop UI as an app, without a console window.

The Windows build's `earwitness-ui.exe` and the `earwitness-ui` command that pip installs run
this; it takes the options of `earwitness ui`. A process started without a console has no
stdout, so its output goes to `earwitness-ui.log` in the working folder (or the temp folder
when that is not writable), and on Windows whatever the user must see comes as a message box:
the browser fallback when the window cannot open, which keeps the server running until the
box is closed, and any error, with the log's path.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

LOG_NAME = "earwitness-ui.log"
MB_OK, MB_ICONERROR, MB_ICONINFORMATION = 0x0, 0x10, 0x40


def message_box(text: str, error: bool = False) -> None:
    """A Windows message box that blocks until closed; elsewhere, a line on stderr."""
    if sys.platform == "win32":
        import ctypes

        flags = MB_OK | (MB_ICONERROR if error else MB_ICONINFORMATION)
        ctypes.windll.user32.MessageBoxW(None, text, "Earwitness", flags)
    elif sys.stderr is not None:
        print(text, file=sys.stderr, flush=True)


def open_log(folder: Path | None = None) -> tuple[TextIO, Path]:
    """Append to the launcher log in `folder` (default the working folder), else in temp."""
    candidates = [(folder or Path.cwd()) / LOG_NAME, Path(tempfile.gettempdir()) / LOG_NAME]
    for path in candidates:
        try:
            stream = path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            continue
        return stream, path.resolve()
    raise OSError("no writable folder for the launcher log")


def keep_running_box(url: str) -> None:
    """Hold the server while the UI runs in the browser: return when the user closes the box."""
    message_box(
        "Earwitness is open in your web browser. (When the desktop window can't open, "
        f"{LOG_NAME} says why.)\n\n"
        f"If no page appeared, open this address:\n{url}\n\n"
        "Earwitness keeps running while this box is open. Click OK to stop it."
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run `earwitness ui` with the given options, logging to a file when there is no console."""
    from earwitness import __version__, cli

    console = sys.stdout is not None and sys.stderr is not None
    log_path: Path | None = None
    if not console:
        stream, log_path = open_log()
        sys.stdout = sys.stderr = stream
        print(f"--- earwitness-ui {__version__} started {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}")
    given = list(sys.argv[1:] if argv is None else argv)
    try:
        args = cli.build_parser().parse_args(["ui", *given])
    except SystemExit as exc:  # bad options, or a file dropped on the program
        if exc.code and not console:
            message_box(
                "Earwitness opens with no file: start it, then open a recording from its pages.\n\n"
                f"It was started with: {' '.join(given)}\n"
                "The options it takes are those of `earwitness ui --help`; "
                f"the details are in {log_path}.",
                error=True,
            )
        return int(exc.code or 0)
    mode = "serve" if args.serve else "browser" if args.browser else "window"
    try:
        from earwitness.server.launch import launch

        return launch(
            cache=args.cache,
            runs_dir=args.runs_dir,
            port=args.port,
            mode=mode,
            wait=None if console else keep_running_box,
        )
    except Exception:
        traceback.print_exc()
        if not console:
            message_box(
                f"Earwitness could not start.\n\n{traceback.format_exc(limit=1)}\n"
                f"The full error is in {log_path}.",
                error=True,
            )
        return 1
