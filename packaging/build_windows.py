"""Build the Windows release: PyInstaller folder, licences, README, smoke test, zip.

  uv sync --group build --no-dev
  uv run --no-sync python packaging/build_windows.py --build-ui --smoke

Writes `dist/windows/earwitness-<version>-windows-x64/` (earwitness.exe, earwitness-ui.exe,
_internal/, LICENSE, README.txt, THIRD_PARTY_LICENSES/) and next to it the zip of that folder
plus a `.sha256` file. `--smoke` runs the built `earwitness.exe --version` and `earwitness.exe
demo --workers 2` in a scratch folder: the install check a clean machine runs, plus a check
that scoring in child processes works in the frozen program. Build in a virtual environment
without the dev group, so only what the package needs at runtime is collected.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "windows"
WORK = ROOT / "build" / "pyinstaller"
LICENSE_FILE = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)


def version() -> str:
    """The version in pyproject.toml."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def run(cmd: list[str], cwd: Path | None = None) -> None:
    """Run a command, echoing it, and stop the build when it fails."""
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, shell=False)


def build_ui() -> None:
    """`npm ci` and `npm run build` in ui/ (Node 22), which write the package's static files."""
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise SystemExit("npm is not on PATH; install Node 22 or build the front end first")
    run([npm, "ci"], cwd=ROOT / "ui")
    run([npm, "run", "build"], cwd=ROOT / "ui")


def runtime_distributions(root: str = "earwitness") -> list[Distribution]:
    """The installed distributions `root` needs at runtime on this platform, itself included:
    its requirements followed recursively, with extras and environment markers applied."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    found: dict[str, tuple[Distribution, frozenset[str]]] = {}
    stack: list[tuple[str, frozenset[str]]] = [(root, frozenset())]
    while stack:
        name, extras = stack.pop()
        key = canonicalize_name(name)
        known = found.get(key)
        if known is not None and extras <= known[1]:
            continue
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        extras = extras | (known[1] if known else frozenset())
        found[key] = (dist, extras)
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker is not None:
                envs = [{"extra": e} for e in sorted(extras)] or [{"extra": ""}]
                if not any(req.marker.evaluate(env) for env in envs):
                    continue
            stack.append((req.name, frozenset(req.extras)))
    return sorted((d for d, _ in found.values()), key=lambda d: canonicalize_name(d.name))


def licence_name(dist: Distribution) -> str:
    """The licence a distribution declares: its SPDX expression, short licence field or
    classifier."""
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return str(expression)
    short = (meta.get("License") or "").strip()
    if short and "\n" not in short and len(short) < 80:
        return short
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(c.split(" :: ")[-1] for c in classifiers)
    return "see the files"


def licence_files(dist: Distribution) -> list[tuple[Path, Path]]:
    """(path inside the distribution's licence folder, source file) for each licence file.

    Every file in the metadata's PEP 639 `licenses/` folder counts, whatever its name (numpy
    keeps LAPACK's and its other bundled libraries' notices there, h5py HDF5's), with its
    relative path kept, since several are named LICENSE.txt and NTFS ignores case. Elsewhere in
    the metadata only files named like licences count.
    """
    found: list[tuple[Path, Path]] = []
    taken: set[str] = set()
    for f in dist.files or []:
        parts = f.parts
        meta = next(
            (i for i, part in enumerate(parts) if part.endswith((".dist-info", ".egg-info"))), None
        )
        if meta is None:
            continue
        rel = Path(*parts[meta + 1 :])
        in_licenses = rel.parts[0] == "licenses" if rel.parts else False
        if not in_licenses and not LICENSE_FILE.search(f.name):
            continue
        src = Path(str(dist.locate_file(f)))
        if not src.is_file():
            continue
        if in_licenses:
            rel = Path(*rel.parts[1:])
        key, n = str(rel).lower(), 1
        while key in taken:  # same name in another case: keep both
            n += 1
            rel = rel.with_name(f"{rel.stem}-{n}{rel.suffix}")
            key = str(rel).lower()
        taken.add(key)
        found.append((rel, src))
    return found


def collect_licences(target: Path) -> int:
    """Copy each bundled distribution's licence files, Python's and PyInstaller's (whose
    bootloader starts both programs) into `target`, with an index. Returns the count."""
    target.mkdir(parents=True, exist_ok=True)
    dists = [d for d in runtime_distributions() if d.name.lower() != "earwitness"]
    dists.append(distribution("pyinstaller"))
    rows = []
    for dist in dists:
        folder = target / f"{dist.name}-{dist.version}"
        copied = 0
        for rel, src in licence_files(dist):
            dest = folder / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            copied += 1
        rows.append(f"{dist.name:<28} {dist.version:<14} {licence_name(dist)}")
        if not copied:
            rows[-1] += "  (no licence file shipped in the wheel)"
    python_licence = Path(sys.base_prefix) / "LICENSE.txt"
    if python_licence.is_file():
        folder = target / f"Python-{sys.version.split()[0]}"
        folder.mkdir(exist_ok=True)
        shutil.copyfile(python_licence, folder / "LICENSE.txt")
        rows.append(f"{'Python':<28} {sys.version.split()[0]:<14} PSF-2.0")
    # MapLibre GL JS and Plotly.js, minified into the front end; ui/vite.config.ts writes these
    front_end = ROOT / "src" / "earwitness" / "server" / "static" / "third-party-licenses.txt"
    if not front_end.is_file():
        raise SystemExit(f"{front_end} is missing: rebuild the front end (npm run build in ui/)")
    (target / "front-end").mkdir(exist_ok=True)
    shutil.copyfile(front_end, target / "front-end" / front_end.name)
    rows.append(f"{'front end (MapLibre, Plotly)':<28} {'':<14} see front-end/{front_end.name}")
    header = (
        "Third-party software in this build\n\n"
        "The Windows build bundles Python and the packages below; each folder here holds the\n"
        "licence files its wheel ships. Libraries inside those wheels (for example GDAL and PROJ\n"
        "in rasterio, HDF5 in h5py, GEOS in shapely, libsndfile in soundfile, OpenBLAS in numpy\n"
        "and scipy) are covered by the notices their wheels carry. No third-party datasets are\n"
        "bundled; see the data-source attribution at https://earwitness.dev/data.html.\n\n"
    )
    (target / "README.txt").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


README = """Earwitness {version} for Windows (64-bit)

Does my recording contain location clues? Earwitness estimates where a recording was made
from background thunder and propeller-aircraft sound, matched against public lightning and
flight records. It is an open-source privacy-awareness proof of concept.

  Site:    https://earwitness.dev
  Source:  https://github.com/cajdata/earwitness

What is in this folder
  earwitness-ui.exe   the desktop app: double-click it.
  earwitness.exe      the command line. Open PowerShell in this folder, then for example:
                        .\\earwitness.exe --help
                        .\\earwitness.exe demo          (checks this install, under a minute)
                        .\\earwitness.exe audit my-recording.wav

The demo makes two synthetic recordings with known answers and locates both, offline. The
desktop app's Results page then shows them.

Where files go: analyses are saved in a runs folder and downloaded public data in data\\cache,
both under the folder you start the program from (this folder, when you double-click). Set
EARWITNESS_CACHE to keep the data cache elsewhere; regional flight data can take tens of GB.

Optional: ffmpeg on PATH lets Earwitness read video and compressed audio (WAV, FLAC and OGG
work without it). The desktop window needs the Microsoft Edge WebView2 Runtime, which
Windows 11 includes; without it the app opens in your web browser instead.

This build is not code-signed, so Windows SmartScreen may warn the first time. See the install
guide: https://earwitness.dev/install.html

Uninstall: delete this folder, and the runs and data folders if you made them elsewhere.

Licence: Apache-2.0 (LICENSE). Third-party licences: THIRD_PARTY_LICENSES\\README.txt.
"""


def smoke(folder: Path) -> None:
    """Run the built program the way the clean-machine check does, in a scratch folder."""
    exe = folder / "earwitness.exe"
    with tempfile.TemporaryDirectory(prefix="earwitness-smoke-") as tmp:
        run([str(exe), "--version"], cwd=Path(tmp))
        run([str(exe), "demo", "--workers", "2"], cwd=Path(tmp))
        run([str(exe), "audit", str(Path(tmp) / "earwitness-demo" / "aircraft" / "recording.wav")])


def make_zip(folder: Path) -> Path:
    """Zip the folder (the folder itself at the top of the archive) and write its SHA-256."""
    archive = folder.parent / f"{folder.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(folder.rglob("*")):
            z.write(path, path.relative_to(folder.parent))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    # LF even on Windows: `sha256sum -c` reads a CR as part of the file name
    archive.with_name(archive.name + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii", newline="\n"
    )
    return archive


def main() -> int:
    """Build, document, check and zip the Windows release."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build-ui", action="store_true", help="run npm ci and build first")
    parser.add_argument("--smoke", action="store_true", help="run the built demo")
    parser.add_argument("--no-zip", action="store_true", help="leave the folder unzipped")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("the Windows build runs on Windows")
    if args.build_ui:
        build_ui()
    ver = version()
    folder = DIST / f"earwitness-{ver}-windows-x64"
    env_note = os.environ.get("VIRTUAL_ENV", sys.prefix)
    print(f"building Earwitness {ver} from {env_note}", flush=True)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(WORK),
            str(ROOT / "packaging" / "earwitness.spec"),
        ]
    )
    shutil.copyfile(ROOT / "LICENSE", folder / "LICENSE")
    (folder / "README.txt").write_text(README.format(version=ver), encoding="utf-8")
    n = collect_licences(folder / "THIRD_PARTY_LICENSES")
    print(f"licences of {n} bundled components in THIRD_PARTY_LICENSES", flush=True)
    if args.smoke:
        smoke(folder)
    if not args.no_zip:
        archive = make_zip(folder)
        size_mb = archive.stat().st_size / 1e6
        print(f"wrote {archive} ({size_mb:.0f} MB) and its .sha256", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
