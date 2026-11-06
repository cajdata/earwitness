# PyInstaller spec of the Windows build: one folder holding earwitness.exe (the command line,
# with a console) and earwitness-ui.exe (the desktop app, without one), which share one
# _internal folder of Python and libraries. Build it with packaging/build_windows.py, which
# also adds the licences and README, smoke-tests the result and zips it (docs/RELEASING.md).
# The front end must be built into src/earwitness/server/static first (npm run build in ui/).
# ruff: noqa
import re
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is set by PyInstaller)
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
VERSION = PROJECT["version"]
STATIC = ROOT / "src" / "earwitness" / "server" / "static"
if not (STATIC / "index.html").is_file():
    raise SystemExit(f"{STATIC} has no index.html: run `npm ci && npm run build` in ui/ first")


def version_info(description: str, name: str) -> VSVersionInfo:
    """The Windows file properties (Details tab) of one executable."""
    numbers = [int(n) for n in re.findall(r"\d+", VERSION)[:3]]
    numbers = tuple(numbers + [0] * (4 - len(numbers)))
    strings = [
        StringStruct("FileDescription", description),
        StringStruct("FileVersion", VERSION),
        StringStruct("InternalName", name),
        StringStruct("LegalCopyright", "Apache-2.0, see LICENSE"),
        StringStruct("OriginalFilename", f"{name}.exe"),
        StringStruct("ProductName", "Earwitness"),
        StringStruct("ProductVersion", VERSION),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=numbers,
            prodvers=numbers,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([StringTable("040904B0", strings)]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


a = Analysis(
    [str(ROOT / "packaging" / "earwitness_cli.py"), str(ROOT / "packaging" / "earwitness_ui.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(STATIC), "earwitness/server/static"), *copy_metadata("earwitness")],
    # the CLI imports each subcommand's modules inside its function; collect them all
    hiddenimports=collect_submodules("earwitness"),
    hookspath=[str(ROOT / "packaging" / "hooks")],  # rasterio
    runtime_hooks=[],
    # Never imported at runtime: test runners that numpy and scipy import only in their test
    # helpers, and setuptools, which cffi imports only to compile extensions.
    excludes=[
        "tkinter",
        "_tkinter",
        "pytest",
        "_pytest",
        "pluggy",
        "IPython",
        "matplotlib",
        "setuptools",
        "_distutils_hack",
        "pkg_resources",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# One analysis serves both programs: each executable gets the runtime hooks plus its own entry
# script, and the shared libraries are collected once.
cli_scripts = [s for s in a.scripts if s[0] != "earwitness_ui"]
ui_scripts = [s for s in a.scripts if s[0] != "earwitness_cli"]

exe_cli = EXE(
    pyz,
    cli_scripts,
    [],
    exclude_binaries=True,
    name="earwitness",
    console=True,
    upx=False,
    version=version_info("Earwitness command line", "earwitness"),
)
exe_ui = EXE(
    pyz,
    ui_scripts,
    [],
    exclude_binaries=True,
    name="earwitness-ui",
    console=False,
    upx=False,
    version=version_info("Earwitness desktop app", "earwitness-ui"),
)
coll = COLLECT(
    exe_cli,
    exe_ui,
    a.binaries,
    a.datas,
    upx=False,
    name=f"earwitness-{VERSION}-windows-x64",
)
