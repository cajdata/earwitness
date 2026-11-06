"""PyInstaller entry point of `earwitness.exe`, the Windows build's command line."""

import multiprocessing
import sys

from earwitness.cli import main

if __name__ == "__main__":
    # `--workers` scores in child processes, which start this executable again; a frozen
    # program must hand those starts to multiprocessing before doing anything else.
    multiprocessing.freeze_support()
    sys.exit(main())
