"""When a recording started, read from the file itself: its name and its container tags.

A search that knows the start to a couple of seconds can place one flyover on its own and
narrows every other search, and phones usually write that time into the file more than once.
The conventions this module reads, with the file kinds they come from:

- Android camera apps name a video by its local start time: `20261104_143750.mp4` (Samsung),
  `VID_20261104_143750.mp4` and `IMG_...` (many others); screen and sound recorders use
  `..._20261104-143750` or `2026-11-04 14-37-50`. Google Pixel names files in UTC:
  `PXL_20261104_213750123.mp4`.
- Android writes the container's `creation_time` (UTC) when recording stops, so the start is
  that minus the clip's length; Samsung adds `com.samsung.android.utc_offset`.
- iPhones write `com.apple.quicktime.creationdate`, the local start time with its offset, and
  a `creation_time` (UTC) at the start.

Where two sources exist they are checked against each other: a file name in local time and a
UTC tag that agree to a few seconds fix the time zone as well. Nothing here is certain (an
edited or re-encoded file keeps none of it, and a phone clock can be wrong), so callers show
the result for the user to confirm, with the reasons, and treat it as a prior.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

AGREE_S = 5.0
"""Two start times this close (seconds) are the same start."""
QUARTER_HOUR_S = 900.0

_NAME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"PXL_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})"), "utc"),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})(?!\d{2})"), "local"),
    (
        re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[.:\-]?(\d{2})[.:\-]?(\d{2})"),
        "local",
    ),
)


@dataclass(frozen=True)
class NameTime:
    """A start time read from a file name: wall-clock (naive) or UTC."""

    time: datetime
    """Naive; local wall-clock time unless `utc`."""
    utc: bool
    text: str
    """The part of the name it came from."""


def name_time(name: str) -> NameTime | None:
    """The start time a camera or recorder app wrote into a file name, if any."""
    for pattern, kind in _NAME_PATTERNS:
        m = pattern.search(name)
        if not m:
            continue
        try:
            t = datetime(*(int(g) for g in m.groups()[:6]))  # type: ignore[misc]
        except ValueError:
            continue
        if not 1990 <= t.year <= 2100:
            continue
        return NameTime(time=t, utc=kind == "utc", text=m.group(0))
    return None


def _parse_iso(text: str) -> datetime | None:
    s = text.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    m = re.match(r"(.*[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2})(\d{2})$", s)
    if m:
        s = f"{m.group(1)}{m.group(2)}:{m.group(3)}"
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t


def _parse_offset(text: str) -> timedelta | None:
    m = re.fullmatch(r"\s*([+-])(\d{2}):?(\d{2})\s*", text)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    return sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))


def _tag(tags: dict[str, str], *keys: str) -> str | None:
    lower = {k.lower(): v for k, v in tags.items()}
    for key in keys:
        if key in lower and str(lower[key]).strip():
            return str(lower[key])
    return None


def _offset_text(offset: timedelta) -> str:
    minutes = round(offset.total_seconds() / 60.0)
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def computer_offset(local: datetime) -> timedelta:
    """This computer's UTC offset at a local wall-clock time, with its own daylight rules."""
    off = local.astimezone().utcoffset()
    return off if off is not None else timedelta(0)


@dataclass(frozen=True)
class StartTime:
    """The recording start the file points to, and why."""

    utc: datetime
    """Aware, UTC."""
    utc_offset: timedelta | None
    """Local offset at the recording, when the file says or two sources fix it."""
    tolerance_s: float
    """How far off it may be: a couple of seconds when exact, half the clip when the file only
    says the start lies somewhere within the clip's own length."""
    exact: bool
    source: str
    """Short label: file name, phone tags, QuickTime date..."""
    note: str
    """One plain sentence for the UI and the CLI."""
    assumed_zone: bool = False
    """True when the time zone came from this computer, not from the file."""

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form; times as ISO strings, the offset in minutes."""
        local = None
        if self.utc_offset is not None:
            local = (self.utc + self.utc_offset).replace(tzinfo=None).isoformat()
        return {
            "start_utc": self.utc.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "local": local,
            "utc_offset_min": None
            if self.utc_offset is None
            else round(self.utc_offset.total_seconds() / 60.0),
            "tolerance_s": self.tolerance_s,
            "exact": self.exact,
            "source": self.source,
            "note": self.note,
            "assumed_zone": self.assumed_zone,
        }


def start_time(
    name: str,
    tags: dict[str, str],
    duration_s: float,
    local_offset: Callable[[datetime], timedelta] | None = computer_offset,
) -> StartTime | None:
    """The best start time the file name and container tags give, or None.

    `tags` are the container and stream tags (any case). `local_offset` gives the UTC offset of
    a local wall-clock time when the file itself does not fix the zone; None refuses to guess.
    """
    nt = name_time(name)
    android = any(k.lower().startswith("com.android.") for k in tags) or any(
        "samsung" in k.lower() for k in tags
    )
    apple = any(k.lower().startswith("com.apple.") for k in tags)
    tag_offset = _parse_offset(_tag(tags, "com.samsung.android.utc_offset") or "")
    qt = _parse_iso(_tag(tags, "com.apple.quicktime.creationdate") or "")
    created = _parse_iso(_tag(tags, "creation_time") or "")
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if created is not None and created.year < 1990:
        created = None  # an unset clock (1904 or 1970 epochs)

    # what the tags say about the start, each with how it was derived
    tag_starts: list[tuple[datetime, str]] = []
    if qt is not None and qt.tzinfo is not None:
        tag_starts.append((qt.astimezone(UTC), "the QuickTime creation date"))
    if created is not None:
        if android:
            tag_starts.append(
                (created - timedelta(seconds=duration_s), "the phone's record of when it stopped")
            )
        elif apple:
            tag_starts.append((created, "the file's creation time"))
    qt_offset = qt.utcoffset() if qt is not None and qt.tzinfo is not None else None
    known_offset = tag_offset or qt_offset

    if nt is not None:
        if nt.utc:
            start = nt.time.replace(tzinfo=UTC)
            offset = known_offset
        elif known_offset is not None:
            start = (nt.time - known_offset).replace(tzinfo=UTC)
            offset = known_offset
        else:
            offset = None
            start = None
            candidates = list(tag_starts)
            if created is not None and not android and not apple:
                candidates += [
                    (created, "the file's creation time"),
                    (created - timedelta(seconds=duration_s), "the file's creation time"),
                ]
            for t, _ in candidates:
                guess = (nt.time - t.replace(tzinfo=None)).total_seconds()
                quarters = round(guess / QUARTER_HOUR_S) * QUARTER_HOUR_S
                if abs(guess - quarters) <= AGREE_S and abs(quarters) <= 14 * 3600:
                    offset = timedelta(seconds=quarters)
                    start = (nt.time - offset).replace(tzinfo=UTC)
                    break
        assumed = False
        if start is None:
            if local_offset is None:
                return None
            offset = local_offset(nt.time)
            start = (nt.time - offset).replace(tzinfo=UTC)
            assumed = True
        agree = [why for t, why in tag_starts if abs((t - start).total_seconds()) <= AGREE_S]
        clock = "UTC" if nt.utc else nt.time.strftime("%H:%M:%S") + " local"
        note = f"Started {clock} by the file name ({nt.text})"
        if offset is not None and not nt.utc:
            note += f", {_offset_text(offset)}"
        if assumed:
            note += "; the time zone is this computer's, so check it"
        if agree:
            note += f"; {agree[0]} agrees"
        return StartTime(
            utc=start,
            utc_offset=offset,
            tolerance_s=2.0,
            exact=True,
            source="file name",
            note=note + ".",
            assumed_zone=assumed,
        )
    if tag_starts:
        start, why = tag_starts[0]
        return StartTime(
            utc=start,
            utc_offset=known_offset,
            tolerance_s=2.0,
            exact=True,
            source="file tags",
            note=f"Started {start:%Y-%m-%d %H:%M:%S} UTC by {why}.",
        )
    if created is not None:
        mid = created - timedelta(seconds=duration_s / 2.0)
        return StartTime(
            utc=mid,
            utc_offset=None,
            tolerance_s=duration_s / 2.0 + AGREE_S,
            exact=False,
            source="file tags",
            note=(
                f"The file was created at {created:%Y-%m-%d %H:%M:%S} UTC, at the start or the"
                " end of the recording; the search allows both."
            ),
        )
    return None


def fixed_zone(offset: timedelta) -> timezone:
    """A tzinfo for a fixed UTC offset."""
    return timezone(offset)
