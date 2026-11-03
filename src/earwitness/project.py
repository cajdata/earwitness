"""Case and run folders: hashing, JSON and UTC helpers.

M1 uses these for simulator case folders (`recording.wav`, `truth.json`, source data). M2 adds
the analysis project folder (priors, events, config, results, run log) so any result can be
reproduced from its folder.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

TRUTH_SCHEMA = "earwitness.truth/1"
STRIKES_SCHEMA = "earwitness.strikes/1"
TRACKS_SCHEMA = "earwitness.tracks/1"


def sha256_file(path: str | Path) -> str:
    """Hex SHA-256 of a file's contents."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def utc_iso(dt: datetime, milliseconds: bool = False) -> str:
    """ISO 8601 UTC with a Z suffix, to seconds or milliseconds."""
    d = dt.astimezone(UTC)
    if milliseconds:
        return d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: str) -> datetime:
    """Parse an ISO 8601 timestamp; naive values are taken as UTC."""
    s = text.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utc_now() -> datetime:
    """Current time, UTC."""
    return datetime.now(tz=UTC)


def write_json(path: str | Path, obj: object) -> Path:
    """Write pretty-printed JSON with a trailing newline."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    return p


def read_json(path: str | Path) -> object:
    """Read a JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
