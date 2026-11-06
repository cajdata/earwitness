"""PyInstaller entry point of `earwitness-ui.exe`, the desktop app without a console window."""

import multiprocessing
import sys

from earwitness.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # see earwitness_cli.py
    sys.exit(main())
