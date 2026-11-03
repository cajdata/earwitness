"""Local data cache: where downloaded public data lives, and a small resilient downloader.

The cache root is, in order: an explicit override, the EARWITNESS_CACHE environment variable,
or `data/cache` under the current directory (the repo's gitignored `data/`).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CACHE = Path("data") / "cache"
USER_AGENT = "earwitness (https://earwitness.dev)"


def cache_root(override: str | Path | None = None) -> Path:
    """Resolve the cache directory without creating it."""
    if override:
        return Path(override)
    env = os.environ.get("EARWITNESS_CACHE")
    return Path(env) if env else DEFAULT_CACHE


def download(url: str, dest: Path, timeout: float = 120.0, retries: int = 3) -> Path:
    """Download `url` to `dest` atomically, retrying transient failures."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(retries):
        tmp_path: Path | None = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with (
                urllib.request.urlopen(req, timeout=timeout) as resp,
                tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp,
            ):
                shutil.copyfileobj(resp, tmp, length=1 << 20)
                tmp_path = Path(tmp.name)
            tmp_path.replace(dest)
            return dest
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"download failed after {retries} attempts: {url}: {last}")


def fetch_text(url: str, timeout: float = 60.0, retries: int = 3) -> str:
    """GET a small text resource (listings, indexes)."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed after {retries} attempts: {url}: {last}")
