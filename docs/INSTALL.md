# Installing Earwitness

Earwitness runs on 64-bit Windows 10 or 11 and on Linux. There are two ways to install it:

- **Windows build**: a zip with the program and everything it needs. No Python required.
- **From PyPI** with pipx or uv, on Windows or Linux: needs Python 3.12 or newer.

Whichever you choose, finish with `earwitness demo`. It checks that the libraries load, then
makes two synthetic recordings with known answers and locates both, offline, in under a
minute. It ends with `demo passed` when the install works.

Nothing is uploaded anywhere. Earwitness downloads public lightning, flight, terrain and map
data to your computer when an analysis needs it (see [DATA_SOURCES.md](DATA_SOURCES.md)), and
everything else stays on your computer.

## Windows build

1. Download `earwitness-0.1.0-windows-x64.zip` and `earwitness-0.1.0-windows-x64.zip.sha256`
   from the [latest release](https://github.com/cajdata/earwitness/releases/latest).
2. Open PowerShell in your Downloads folder and check the download against its checksum. The
   two values must match:

   ```powershell
   (Get-FileHash .\earwitness-0.1.0-windows-x64.zip -Algorithm SHA256).Hash
   Get-Content .\earwitness-0.1.0-windows-x64.zip.sha256
   ```

3. Unblock the zip, then extract it. Unblocking first stops Windows from asking about every
   program inside it:

   ```powershell
   Unblock-File .\earwitness-0.1.0-windows-x64.zip
   Expand-Archive .\earwitness-0.1.0-windows-x64.zip -DestinationPath $env:USERPROFILE\Earwitness
   ```

4. Run the install check:

   ```powershell
   cd $env:USERPROFILE\Earwitness\earwitness-0.1.0-windows-x64
   .\earwitness.exe demo
   ```

5. Double-click `earwitness-ui.exe` in that folder to open the desktop app. Its Results page
   lists the two demo runs. You can pin it to Start or the taskbar like any program.

**SmartScreen.** The build is not code-signed. If you skipped `Unblock-File`, Windows may show
"Windows protected your PC" the first time. Click **More info**, then **Run anyway**. If you'd
rather not, install from PyPI instead (below).

**The desktop window** uses the Microsoft Edge WebView2 Runtime. Windows 11 includes it, and
most Windows 10 PCs get it with Edge. Without it, Earwitness opens in your web browser instead
and shows a small box: keep the box open while you work, and click OK to stop the program. To
get the window, install the Evergreen Runtime from
[Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/).

**Command line.** `earwitness.exe` is the full command-line tool, for example
`.\earwitness.exe audit my-recording.wav`. To run it from any folder, add the program folder to
your user `PATH`.

## From PyPI (Windows or Linux)

You need Python 3.12 or newer and [pipx](https://pipx.pypa.io) (or
[uv](https://docs.astral.sh/uv/)).

```bash
pipx install earwitness
```

or, with uv:

```bash
uv tool install earwitness
```

Then check the install:

```bash
earwitness demo
```

This installs two commands. `earwitness` is the command-line tool, and `earwitness-ui` opens
the desktop app without a terminal window. `earwitness ui` does the same from a terminal and
prints its log there.

### Linux notes

- On Debian or Ubuntu, install pipx with `sudo apt install pipx`, then run `pipx ensurepath`
  and open a new terminal. Ubuntu 24.04 and newer ship Python 3.12.
- The terrain library (rasterio) needs the system's expat library. Desktop installs have it;
  minimal ones, such as Docker's slim images, need `sudo apt install libexpat1`. Without it,
  `earwitness demo` fails its `terrain tiles` check.
- `earwitness ui --browser` opens the app in your web browser and works on any desktop.
- For a desktop window instead, give pywebview a toolkit. Qt is the simplest, at about
  200 MB of wheels:

  ```bash
  pipx inject earwitness "pywebview[qt]"
  ```

  GTK works too (`pywebview[gtk]`), but needs the system's PyGObject and WebKit2GTK
  development packages to build. If neither toolkit is present, `earwitness ui` falls back to
  the browser on its own.

## Optional: ffmpeg

With [ffmpeg](https://ffmpeg.org) on `PATH`, Earwitness can read video files and compressed
audio (MP4, MOV, M4A, MP3 and others), and the audit reads a file's metadata tags. Without it,
WAV, FLAC and OGG still work. The demo and the Windows build don't need it.

- Windows: `winget install --id Gyan.FFmpeg -e`, then open a new terminal.
- Debian or Ubuntu: `sudo apt install ffmpeg`.

## Where files go

- **Analyses**: each run is a folder under `runs`, in the folder you start Earwitness from.
  Change it with `--runs-dir`.
- **Downloaded data**: under `data\cache` (Windows) or `data/cache` (Linux), also in the
  folder you start from. Set the `EARWITNESS_CACHE` environment variable, or pass `--cache`, to
  keep it somewhere else. Plan for the space:

  | data | downloaded | kept |
  | --- | --- | --- |
  | GOES lightning (GLM) | about 100 MB per hour of data | a few MB per hour |
  | adsb.lol flights | about 4 GB per day | a few hundred MB to several GB per day and region |
  | Terrain tiles (Copernicus GLO-30) | about 40 MB per 1 degree tile | the same |
  | Land mask, geoid, aircraft types | a few MB, once | the same |

  A regional aircraft search can also use tens of GB of memory and temporary space while it
  runs. See the [known limitations](release-notes/v0.1.0.md).

## Uninstall

- Windows build: delete the program folder, plus any `runs` and `data` folders Earwitness
  made where you ran it.
- pipx: `pipx uninstall earwitness`. uv: `uv tool uninstall earwitness`.

## From source

For development: [uv](https://docs.astral.sh/uv/) and Node 22.

```bash
git clone https://github.com/cajdata/earwitness
```

```bash
cd earwitness/ui && npm ci && npm run build && cd ..
```

```bash
uv sync
```

```bash
uv run earwitness demo
```

[CONTRIBUTING.md](../CONTRIBUTING.md) has the conventions, and [RELEASING.md](RELEASING.md)
covers the Windows build and releases.
