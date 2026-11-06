"""Shortlist public field recordings of thunder for a real thunder case.

Searches the Internet Archive's mirror of the radio aporee sound map for recordings that mention
thunder or storms, keeps those with a published position inside GOES GLM coverage on a date GLM
was operational, under a public-domain mark or a Creative Commons licence, and rates the storm
at the stated time from a sample of GLM files (one 20 s file every few minutes, so a candidate
costs a few tens of megabytes rather than a hundred per hour): the thunder method needs a few
distinct claps a minute within earshot, not a dense storm whose claps overlap, and claps from
within about 10 km rather than rumble from 25 km, so each class is ranked by the flashes within
10 km during the recording first.

The rows carry positions, so the output folder belongs under data/ (never committed):

    uv run python scripts/m6_thunder_shortlist.py --out data/m6/shortlist

Writes items.json (search hits with parsed positions and times), meta/<id>.json (item metadata),
glm/<id>.json (sampled flashes near each site) and shortlist.json / shortlist.md. Reruns reuse
what is already there.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from earwitness.core import geodesy
from earwitness.data import glm
from earwitness.data.cache import download

SEARCH_WORDS = (
    "thunder", "thunderstorm", "tormenta", "trovoada", "trovão", "trovões", "tempestade",
    "lightning", "storm", "orage", "tonnerre", "trueno", "truenos", "relámpago", "gewitter",
)  # fmt: skip
THUNDER = re.compile(
    r"thunder|trov[aã]o|trov[oõ]es|trovoada|tormenta|trueno|rel[aá]mpago|lightning|orage|"
    r"tonnerre|gewitter|donner|rayo",
    re.IGNORECASE,
)
SUB_SATELLITE_LON = {"east": -75.2, "west": -137.2}
GLM_REACH_DEG = 55.0
"""Great-circle radius around the sub-satellite point treated as inside the GLM field of view."""
FILE_S = 20.0
USER_AGENT = "earwitness-validation/0.1 (+https://github.com/cajdata/earwitness)"


def fetch_json(url: str) -> dict:
    """GET a JSON document."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def search(rows: int = 5000) -> list[dict]:
    """radio aporee items whose text mentions a storm word, with title, date and coverage."""
    words = " OR ".join(SEARCH_WORDS)
    q = f"identifier:aporee_* AND ({words})"
    params = [("q", q), ("rows", str(rows)), ("page", "1"), ("output", "json")]
    params += [("fl[]", f) for f in ("identifier", "title", "date", "coverage")]
    url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(params)
    return fetch_json(url)["response"]["docs"]


def parse_coverage(cov: str) -> tuple[float, float, datetime] | None:
    """(lat, lon, UTC start) from an aporee coverage string, or None when incomplete."""
    m = re.search(r"latitude=([-0-9.]+);\s*longitude=([-0-9.]+)", cov or "")
    d = re.search(r"date=(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ([A-Za-z_/+-]+)", cov or "")
    if not m or not d:
        return None
    try:
        local = datetime.strptime(d.group(1), "%Y-%m-%d %H:%M:%S")
        start = local.replace(tzinfo=ZoneInfo(d.group(2))).astimezone(UTC)
    except (ValueError, KeyError):
        return None
    return float(m.group(1)), float(m.group(2)), start


def central_angle_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle angle between two points, degrees."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(
        math.radians(lon2 - lon1)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def glm_satellite(lat: float, lon: float, day: date) -> str | None:
    """Operational GLM satellite that sees a point on a day, East preferred; None outside."""
    for slot in ("east", "west"):
        if central_angle_deg(lat, lon, 0.0, SUB_SATELLITE_LON[slot]) > GLM_REACH_DEG:
            continue
        try:
            return glm.satellite_for(day, slot)
        except ValueError:
            continue
    return None


def length_s(value: object) -> float | None:
    """Audio length from archive metadata: seconds, or mm:ss / hh:mm:ss."""
    if value is None:
        return None
    s = str(value)
    try:
        if ":" in s:
            parts = [float(p) for p in s.split(":")]
            out = 0.0
            for p in parts:
                out = out * 60.0 + p
            return out
        return float(s)
    except ValueError:
        return None


def item_meta(ident: str, cache: Path) -> dict:
    """Licence, description and audio files of an item (cached as JSON)."""
    path = cache / f"{ident}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    d = fetch_json(f"https://archive.org/metadata/{urllib.parse.quote(ident)}")
    md = d.get("metadata", {})
    audio = []
    for f in d.get("files", []):
        name = str(f.get("name", ""))
        if name.lower().endswith((".mp3", ".ogg", ".flac", ".wav", ".m4a")):
            audio.append(
                {
                    "name": name,
                    "length_s": length_s(f.get("length")),
                    "size": int(f.get("size", 0) or 0),
                    "format": f.get("format"),
                }
            )
    desc = re.sub(r"<[^>]+>", " ", str(md.get("description", "")))
    out = {
        "identifier": ident,
        "title": md.get("title"),
        "licenseurl": md.get("licenseurl"),
        "description": re.sub(r"\s+", " ", desc).strip(),
        "audio": audio,
    }
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.3)
    return out


def licence_ok(url: str | None) -> bool:
    """Public-domain mark, CC0 or any Creative Commons licence."""
    u = (url or "").lower()
    return "creativecommons.org/publicdomain" in u or "creativecommons.org/licenses" in u


def key_start(key: str) -> datetime | None:
    """Start time encoded in a GLM file name (_sYYYYDDDHHMMSSt)."""
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)", key)
    if not m:
        return None
    y, doy, hh, mm, ss, tenth = (int(g) for g in m.groups())
    base = datetime(y, 1, 1, tzinfo=UTC) + timedelta(days=doy - 1)
    return base + timedelta(hours=hh, minutes=mm, seconds=ss + tenth / 10.0)


def sample_glm(
    sat: str,
    t_lo: datetime,
    t_hi: datetime,
    lat: float,
    lon: float,
    every: int,
    scratch: Path,
    radius_m: float = 50000.0,
) -> dict:
    """Flashes within `radius_m` of a site from every `every`-th GLM file in [t_lo, t_hi]."""
    keys: list[tuple[datetime, str]] = []
    cursor = t_lo.replace(minute=0, second=0, microsecond=0)
    while cursor < t_hi:
        for key, _ in glm.list_hour(sat, cursor.date(), cursor.hour):
            t = key_start(key)
            if t is not None and t_lo <= t < t_hi:
                keys.append((t, key))
        cursor += timedelta(hours=1)
    keys.sort()
    picked = keys[::every]
    scratch.mkdir(parents=True, exist_ok=True)

    def one(item: tuple[datetime, str]) -> tuple[float, list[float], list[float]]:
        t, key = item
        path = download(glm.object_url(sat, key), scratch / Path(key).name)
        try:
            b = glm.read_glm_file(path)
        finally:
            path.unlink(missing_ok=True)
        good = b.quality == 0
        d = geodesy.horizontal_distance(
            b.lat[good].astype(float), b.lon[good].astype(float), lat, lon
        )
        near = d <= radius_m
        times = (b.time_ns[good][near] / 1e9).tolist()
        return t.timestamp(), times, d[near].tolist()

    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(one, picked))
    return {
        "satellite": sat,
        "every": every,
        "files_listed": len(keys),
        "file_starts": [g[0] for g in got],
        "flash_t": [t for g in got for t in g[1]],
        "flash_d": [d for g in got for d in g[2]],
    }


def rates(sample: dict, t_lo: float, t_hi: float) -> dict:
    """Flash counts and per-minute rates within 10, 25 and 50 km over sampled time in a window."""
    starts = np.asarray(sample["file_starts"], dtype=float)
    inside = (starts >= t_lo) & (starts < t_hi)
    minutes = float(inside.sum()) * FILE_S / 60.0
    t = np.asarray(sample["flash_t"], dtype=float)
    d = np.asarray(sample["flash_d"], dtype=float)
    k = (t >= t_lo) & (t < t_hi)
    out: dict[str, float | int | None] = {"sampled_min": round(minutes, 1)}
    for r_km in (10, 25, 50):
        n = int((k & (d <= r_km * 1000.0)).sum())
        out[f"n{r_km}"] = n
        out[f"rate{r_km}"] = round(n / minutes, 2) if minutes > 0 else None
    out["nearest_km"] = round(float(d[k].min()) / 1000.0, 1) if k.any() else None
    return out


DENSE_PER_MIN = 4.0
"""Flashes a minute within 25 km above which claps overlap into a rolling envelope (both public
thunder cases failed at 10 to 20 a minute; the M2 synthetic storms at 20 or more)."""


def classify(rec: dict, pad: dict) -> str:
    """From the sampled flashes within 25 km: 'dense' above DENSE_PER_MIN during the recording
    or the padded window, 'sparse' with flashes during the recording, 'quiet' with flashes
    only before or after it, 'distant' with none within 25 km but some within 50 km, 'none'."""
    r_rec = rec.get("rate25") or 0.0
    r_pad = pad.get("rate25") or 0.0
    if (pad.get("n50") or 0) == 0:
        return "none"
    if (pad.get("n25") or 0) == 0:
        return "distant"
    if max(r_rec, r_pad) > DENSE_PER_MIN:
        return "dense"
    return "sparse" if (rec.get("n25") or 0) > 0 else "quiet"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="output folder, under data/")
    ap.add_argument("--every", type=int, default=6, help="sample one GLM file in N (default 6)")
    ap.add_argument("--pad-min", type=float, default=60.0, help="minutes before and after")
    ap.add_argument("--default-length-min", type=float, default=20.0)
    ap.add_argument("--skip", nargs="*", default=[], help="identifiers already used")
    ap.add_argument("--no-glm", action="store_true", help="stop after the metadata pass")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    items_path = out / "items.json"
    if items_path.exists():
        items = json.loads(items_path.read_text(encoding="utf-8"))
    else:
        docs = search()
        items = []
        for doc in docs:
            parsed = parse_coverage(str(doc.get("coverage") or ""))
            if parsed is None:
                continue
            lat, lon, start = parsed
            sat = glm_satellite(lat, lon, start.date())
            if sat is None:
                continue
            items.append(
                {
                    "identifier": doc["identifier"],
                    "title": doc.get("title"),
                    "lat": lat,
                    "lon": lon,
                    "start_utc": start.isoformat(),
                    "satellite": sat,
                }
            )
        items_path.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{len(docs)} search hits, {len(items)} inside GLM coverage", flush=True)

    rows = []
    for it in items:
        if it["identifier"] in args.skip:
            continue
        meta = item_meta(it["identifier"], out / "meta")
        text = f"{it.get('title') or ''} {meta.get('title') or ''} {meta['description']}"
        if not THUNDER.search(text):
            continue
        if not licence_ok(meta.get("licenseurl")):
            print(f"{it['identifier']}: licence {meta.get('licenseurl')!r}, skipped", flush=True)
            continue
        lengths = [a["length_s"] for a in meta["audio"] if a["length_s"]]
        rows.append({**it, "licenseurl": meta.get("licenseurl"), "audio": meta["audio"],
                     "length_s": max(lengths) if lengths else None})  # fmt: skip
    print(f"{len(rows)} items mention thunder under an open licence", flush=True)
    if args.no_glm:
        return

    for row in rows:
        start = datetime.fromisoformat(row["start_utc"])
        length = row["length_s"] or args.default_length_min * 60.0
        end = start + timedelta(seconds=length)
        pad = timedelta(minutes=args.pad_min)
        path = out / "glm" / f"{row['identifier']}.json"
        if path.exists():
            sample = json.loads(path.read_text(encoding="utf-8"))
        else:
            t1 = time.perf_counter()
            try:
                sample = sample_glm(
                    row["satellite"], start - pad, end + pad, row["lat"], row["lon"],
                    args.every, out / "glm" / "_raw",
                )  # fmt: skip
            except Exception as exc:  # keep going through the list
                print(f"{row['identifier']}: GLM sample failed: {exc}", flush=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sample), encoding="utf-8")
            print(
                f"{row['identifier']}: {len(sample['file_starts'])} files sampled, "
                f"{len(sample['flash_t'])} flashes within 50 km "
                f"({time.perf_counter() - t1:.0f} s)",
                flush=True,
            )
        row["recording"] = rates(sample, start.timestamp(), end.timestamp())
        row["padded"] = rates(sample, (start - pad).timestamp(), (end + pad).timestamp())
        row["class"] = classify(row["recording"], row["padded"])

    rated = [r for r in rows if "class" in r]
    order = {"sparse": 0, "quiet": 1, "dense": 2, "distant": 3, "none": 4}
    # within a class, claps from within 10 km first: the five sparse storms first analysed had
    # four or fewer there and failed, their thunder rumble from 10 to 25 km
    rated.sort(
        key=lambda r: (
            order[r["class"]],
            -(r["recording"].get("n10") or 0),
            -(r["recording"].get("n25") or 0),
            -(r["length_s"] or 0.0),
        )
    )
    (out / "shortlist.json").write_text(
        json.dumps(rated, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        "| class | item | start (UTC) | length (min) | rate 25 km rec / padded (per min) | "
        "n10 / n25 rec | nearest (km) | licence | title |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rated:
        rec, pad = r["recording"], r["padded"]
        lic = (r.get("licenseurl") or "").split("creativecommons.org/")[-1].strip("/")
        lines.append(
            f"| {r['class']} | {r['identifier']} | {r['start_utc'][:16]} | "
            f"{(r['length_s'] or 0) / 60:.0f} | {rec.get('rate25')} / {pad.get('rate25')} | "
            f"{rec.get('n10')} / {rec.get('n25')} | {rec.get('nearest_km')} | {lic} | "
            f"{(r.get('title') or '')[:60]} |"
        )
    (out / "shortlist.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    main()
