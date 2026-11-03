"""Case and run folders: hashing, JSON, a small TOML writer, UTC helpers, and the run log.

Simulator case folders hold `recording.wav`, `truth.json` and source data. Analysis run folders
hold priors, config, results, the heat map, overlays and a run log, so any result can be
reproduced from its folder.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
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


def parse_utc_date(text: str) -> datetime:
    """Parse YYYY-MM-DD (or a full timestamp) into a UTC datetime at the start of that instant."""
    s = text.strip()
    if len(s) == 10:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
    return parse_utc(s)


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


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if value is None:
        return '"null"'
    if isinstance(value, datetime):
        return json.dumps(utc_iso(value))
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value))


def toml_dumps(mapping: Mapping[str, object]) -> str:
    """Serialize a flat-or-one-level-nested mapping as TOML (enough for config files)."""
    lines: list[str] = []
    tables: list[tuple[str, Mapping[str, object]]] = []
    for key, value in mapping.items():
        if isinstance(value, Mapping):
            tables.append((key, value))
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    for name, table in tables:
        if lines:
            lines.append("")
        lines.append(f"[{name}]")
        for key, value in table.items():
            lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


class RunLog:
    """Append-only run log with UTC timestamps, optionally echoed to a callback."""

    def __init__(self, path: str | Path, echo: Callable[[str], None] | None = None) -> None:
        self.path = Path(path)
        self.echo = echo
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        """Record one line."""
        line = f"{utc_iso(utc_now(), milliseconds=True)} {message}"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.echo:
            self.echo(message)
