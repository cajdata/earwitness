"""Aircraft type designators to engine class: piston, turboprop, jet, helicopter.

The ADS-B traces carry each aircraft's ICAO type designator (`t`, e.g. C172 or P28A). The ICAO
DOC 8643 description string, such as L1P or H2T, gives the vehicle kind (L landplane, S seaplane,
A amphibian, H helicopter, G gyrocopter, T tiltrotor), the engine count and the engine type
(P piston, T turboprop, J jet, E electric, R rocket). The table shipped with dump1090 and tar1090
(`icao_aircraft_types.json`, about 2,600 designators) is downloaded and cached. ICAO's own
DOC 8643 site does not serve automated requests, so a DOC 8643 JSON export can be dropped into
the cache by hand as `aircraft_types/icao_doc8643.json` and takes precedence when present.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

from earwitness.data.cache import cache_root, download

DUMP1090_URL = (
    "https://raw.githubusercontent.com/flightaware/dump1090/master/public_html/db/"
    "aircraft_types/icao_aircraft_types.json"
)

ENGINE_CLASSES = {"P": "piston", "T": "turboprop", "J": "jet", "E": "electric", "R": "rocket"}

# Designators that entered service after the dump1090 table was compiled. ICAO DOC 8643
# descriptions, kept short on purpose; anything else unknown can be supplied through
# `aircraft_types/supplement.json` in the cache ({"CODE": "L1P", ...}).
BUILTIN_SUPPLEMENT: dict[str, str] = {
    "A20N": "L2J",  # Airbus A320neo
    "A21N": "L2J",  # Airbus A321neo
    "A19N": "L2J",  # Airbus A319neo
    "A339": "L2J",  # Airbus A330-900neo
    "A338": "L2J",  # Airbus A330-800neo
    "A35K": "L2J",  # Airbus A350-1000
    "B37M": "L2J",  # Boeing 737 MAX 7
    "B38M": "L2J",  # Boeing 737 MAX 8
    "B39M": "L2J",  # Boeing 737 MAX 9
    "B3XM": "L2J",  # Boeing 737 MAX 10
    "B78X": "L2J",  # Boeing 787-10
    "E290": "L2J",  # Embraer E190-E2
    "E295": "L2J",  # Embraer E195-E2
    "E275": "L2J",  # Embraer E175-E2
    "C700": "L2J",  # Cessna Citation Longitude
    "GL7T": "L2J",  # Bombardier Global 7500
    "GA7C": "L2J",  # Gulfstream G700
    "B505": "H1T",  # Bell 505 Jet Ranger X
    "K100": "L1T",  # Daher Kodiak 100
}
VEHICLE_KINDS = {
    "L": "landplane",
    "S": "seaplane",
    "A": "amphibian",
    "H": "helicopter",
    "G": "gyrocopter",
    "T": "tiltrotor",
}


@dataclass(frozen=True)
class TypeInfo:
    """One designator's description, split into its parts."""

    code: str
    desc: str
    wtc: str = ""
    name: str = ""

    @property
    def vehicle(self) -> str:
        """landplane, helicopter, ... or 'unknown'."""
        return VEHICLE_KINDS.get(self.desc[:1], "unknown") if self.desc else "unknown"

    @property
    def engines(self) -> int | None:
        """Engine count when the description carries one."""
        return int(self.desc[1]) if len(self.desc) >= 2 and self.desc[1].isdigit() else None

    @property
    def engine_type(self) -> str:
        """piston, turboprop, jet, electric, rocket or 'unknown'."""
        return ENGINE_CLASSES.get(self.desc[2:3], "unknown") if len(self.desc) >= 3 else "unknown"


def engine_class(desc: str | None) -> str:
    """Class used by the funnel from a description like L1P: piston, turboprop, jet, helicopter,
    other, or unknown."""
    if not desc or len(desc) < 3:
        return "unknown"
    if desc[0] == "H":
        return "helicopter"
    if desc[0] not in ("L", "S", "A"):
        return "other"
    return {"P": "piston", "T": "turboprop", "J": "jet"}.get(desc[2], "other")


class TypeTable:
    """Designator lookup built from one or more cached tables."""

    def __init__(self, entries: dict[str, TypeInfo], source: str = "") -> None:
        self.entries = entries
        self.source = source

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, code: str) -> TypeInfo | None:
        """Type info for a designator, case-insensitive."""
        return self.entries.get(code.strip().upper()) if code else None

    def engine_class(self, code: str) -> str:
        """Funnel class for a designator; 'unknown' when it is not in the table."""
        info = self.get(code)
        return engine_class(info.desc) if info else "unknown"

    @classmethod
    def from_icao_export(cls, path: Path) -> TypeTable:
        """Parse the ICAO DOC 8643 JSON export (a list of records)."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries: dict[str, TypeInfo] = {}
        for rec in data:
            code = str(rec.get("Designator", "")).strip().upper()
            desc = str(rec.get("Description", "") or "").strip().upper()
            if not code or not desc:
                continue
            name = f"{rec.get('ManufacturerCode', '')} {rec.get('ModelFullName', '')}".strip()
            # Designators repeat across manufacturers; keep the first description seen.
            entries.setdefault(code, TypeInfo(code, desc, str(rec.get("WTC", "") or ""), name))
        return cls(entries, "ICAO DOC 8643")

    @classmethod
    def from_dump1090(cls, path: Path) -> TypeTable:
        """Parse dump1090's icao_aircraft_types.json (designator -> {desc, wtc})."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = {
            code.upper(): TypeInfo(
                code.upper(), str(v.get("desc", "")).upper(), str(v.get("wtc", ""))
            )
            for code, v in data.items()
            if v.get("desc")
        }
        return cls(entries, "dump1090 icao_aircraft_types.json")

    @classmethod
    def load(cls, root: Path | None = None, allow_download: bool = True) -> TypeTable:
        """A hand-placed ICAO export merged over the dump1090 table (downloaded when missing)."""
        base = cache_root(root) / "aircraft_types"
        icao_path = base / "icao_doc8643.json"
        d1090_path = base / "icao_aircraft_types.json"
        tables: list[TypeTable] = []
        if icao_path.exists():
            tables.append(cls.from_icao_export(icao_path))
        if not d1090_path.exists() and allow_download:
            with contextlib.suppress(RuntimeError):
                download(DUMP1090_URL, d1090_path)
        if d1090_path.exists():
            tables.append(cls.from_dump1090(d1090_path))
        if not tables:
            raise RuntimeError("no aircraft type table available; check network access")
        supplement = {code: TypeInfo(code, desc) for code, desc in BUILTIN_SUPPLEMENT.items()}
        user_path = base / "supplement.json"
        if user_path.exists():
            extra = json.loads(user_path.read_text(encoding="utf-8"))
            supplement.update(
                {c.upper(): TypeInfo(c.upper(), str(d).upper()) for c, d in extra.items()}
            )
        tables.append(cls(supplement, "built-in supplement"))
        merged: dict[str, TypeInfo] = {}
        for table in reversed(tables):  # later tables (fallbacks) first, so the ICAO export wins
            merged.update(table.entries)
        return cls(merged, " + ".join(t.source for t in tables))


def classify(code: str, types: TypeTable | None) -> str:
    """Engine class for a designator, or 'unknown' without a table or a match."""
    if types is None or not code:
        return "unknown"
    return types.engine_class(code)
