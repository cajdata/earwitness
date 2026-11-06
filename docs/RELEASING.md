# Releasing

A release is a version tag. `.github/workflows/publish.yml` then builds and tests the wheel and
sdist on Linux and the Windows build on Windows, waits for approval of the `pypi` environment,
publishes to PyPI through trusted publishing (no API token is stored anywhere), and creates the
GitHub Release with `docs/release-notes/<tag>.md` as its notes and every file attached. The
site, earwitness.dev, deploys separately from `site/` on every push to `main` that changes it.

## One-time setup

### PyPI (done for 0.0.1)

1. A PyPI account with 2FA.
2. On PyPI, a trusted publisher for the project (Your projects -> Publishing): owner `cajdata`,
   repository `earwitness`, workflow `publish.yml`, environment `pypi`.
3. In the GitHub repository, an environment named `pypi` (Settings -> Environments) with the
   owner as required reviewer, so nothing publishes without a click.

### The site: GitHub Pages with earwitness.dev

GitHub's order matters: add the custom domain to the Pages settings before creating the DNS
records that point at GitHub, and keep the domain verified, so nobody else can claim it.

1. **Verify the domain for the account** (done: the TXT record is in place). Profile Settings ->
   Pages -> Add a domain shows a TXT record named `_github-pages-challenge-cajdata` for
   `earwitness.dev`. Keep that record for good.
2. **Turn Pages on.** Repository Settings -> Pages -> Build and deployment -> Source:
   **GitHub Actions**. The `Pages` workflow (`.github/workflows/pages.yml`) checks `site/` and
   deploys it. No `CNAME` file is needed with a workflow; GitHub ignores one.
3. **Set the custom domain.** Same page -> Custom domain: `earwitness.dev` -> Save.
4. **DNS at the registrar** (Porkbun). Remove any default parking records for `@` or `www`,
   then add:

   | Type | Host | Answer |
   | --- | --- | --- |
   | A | (blank, the apex) | 185.199.108.153 |
   | A | (blank) | 185.199.109.153 |
   | A | (blank) | 185.199.110.153 |
   | A | (blank) | 185.199.111.153 |
   | AAAA | (blank) | 2606:50c0:8000::153 |
   | AAAA | (blank) | 2606:50c0:8001::153 |
   | AAAA | (blank) | 2606:50c0:8002::153 |
   | AAAA | (blank) | 2606:50c0:8003::153 |
   | CNAME | www | cajdata.github.io |

   No wildcard (`*`) record. The existing CAA records already allow `letsencrypt.org`, which
   issues the Pages certificate. GitHub redirects `www.earwitness.dev` to `earwitness.dev`.
5. **Check DNS** (changes can take up to 24 hours, usually minutes):

   ```powershell
   Resolve-DnsName earwitness.dev -Type A
   Resolve-DnsName earwitness.dev -Type AAAA
   Resolve-DnsName www.earwitness.dev -Type CNAME
   ```

6. **HTTPS.** Once the DNS check on the Pages settings page passes, GitHub requests a Let's
   Encrypt certificate. When it is issued, tick **Enforce HTTPS**. `.dev` domains only work over
   HTTPS, so the site is reachable only from then on. If nothing happens several minutes after
   saving the domain, Remove it and Save it again.

## Cutting a release

1. **Version and notes.** Bump the version in `pyproject.toml` (and `uv lock`), in
   `ui/package.json` and in the root entries of `ui/package-lock.json`. Write
   `docs/release-notes/v<version>.md` with what changed and the known limitations; its links
   must be absolute URLs, because GitHub shows the file as the release page. Update the README
   status and the site's version line. Commit, push, and wait for CI to pass. A push that
   changes `site/` publishes it at once, so push site changes for a release shortly before the
   tag, or keep them on a branch until then: the site's install links point at the latest
   release and at PyPI.
2. **Optional dry run.** Actions -> Windows build -> Run workflow on `main` builds and
   smoke-tests the Windows zip the same way the release will, as a check before tagging. The
   release builds its own zip, and PyInstaller builds are not byte-for-byte reproducible, so
   the dry run's checksum will differ from the release's.
3. **Tag and push:**

   ```bash
   git tag v0.1.0
   ```

   ```bash
   git push origin v0.1.0
   ```

   The Release workflow checks that the tag matches the version and has release notes, runs
   the tests, builds both packages and runs the frozen demo, then waits for approval of the
   `pypi` environment.
4. **Clean-machine check of the release's own zip** (below), while the approval waits: from
   the workflow run's page, download its `windows` artifact, which holds the zip and checksum
   the release will attach.
5. **Approve** the `pypi` deployment. The workflow publishes to PyPI and creates the GitHub
   Release with the notes and every file.

## The Windows build

`packaging/` holds the PyInstaller spec, the two entry scripts, a rasterio hook (it has none of
its own) and `build_windows.py`, which builds the one-folder program, adds `LICENSE`,
`README.txt` and `THIRD_PARTY_LICENSES/` (each bundled package's licence files, Python's,
PyInstaller's and the front end's), runs the smoke test and zips the folder. Build in an
environment without the dev group, as CI does, so nothing from it is collected; `uv sync`
afterwards restores the development environment:

```bash
uv sync --group build --no-dev
```

```bash
uv run --no-sync python packaging/build_windows.py --build-ui --smoke
```

The output is `dist/windows/earwitness-<version>-windows-x64.zip` and its `.sha256`.
`--smoke` runs `earwitness.exe demo --workers 2`, whose install checks load GDAL and PROJ, the
GLM NetCDF reader, Parquet and GEOS inside the frozen program, then the two synthetic cases in
child processes, and an audit. A frozen program misses modules that are imported in ways
PyInstaller can't see; when one is missing, the demo's install checks fail first. Add a hook
in `packaging/hooks/` when that happens. The build is not code-signed.

## Clean-machine check

The M7 acceptance check: a clean Windows machine installs the build and runs the synthetic
demo.

**With Windows Sandbox** (Windows 10 or 11 Pro, Enterprise or Education). Enable it once, as
administrator, then restart:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All
```

Then, from the repository:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\sandbox\start-clean-machine-test.ps1 -Zip dist\windows\earwitness-0.1.0-windows-x64.zip
```

The sandbox is a fresh Windows with no Python, ffmpeg or developer tools. Inside it,
`clean-machine-test.ps1` follows docs/INSTALL.md: it marks the zip as downloaded, checks the
checksum, unblocks and extracts it, runs `earwitness.exe --version`, `earwitness.exe demo
--workers 2` and an audit, starts `earwitness-ui.exe` as a double-click would, and checks that
the app serves its page and lists the two demo runs. It writes `clean-machine-test.log` and
`summary.json` to `sandbox-results/` on the host and leaves the app open: its Results page
should show `demo-thunder` and `demo-aircraft` on a map. Closing the sandbox discards it all.

**On another PC or VM**, follow the Windows steps of [INSTALL.md](INSTALL.md) exactly and
check that:

1. the checksum matches;
2. `.\earwitness.exe demo` ends with `demo passed`, its `install` lines read `ok` (the desktop
   window line may read `--` without WebView2, and ffmpeg's will without ffmpeg);
3. double-clicking `earwitness-ui.exe` opens the app (or, without WebView2, the browser and a
   small box), and its Results page lists the two demo runs.
