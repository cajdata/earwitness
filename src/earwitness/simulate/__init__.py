"""Forward simulator: synthetic recordings with a known answer (M1).

`write_thunder_case` and `write_aircraft_case` render a scenario, mix it like a real recording,
and write a case folder:

- `recording.wav`: mono PCM-16 at the chosen rate, peak at -6 dBFS
- `truth.json`: observer location, start time, every event with its heard timing, the physics
  constants and mixing parameters used, and the recording's SHA-256
- `strikes.json` or `tracks.json`: the synthetic source data in the shape the search pipelines
  will read in place of downloaded GLM or adsb.lol data
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from earwitness import __version__
from earwitness.audio.decode import write_wav
from earwitness.project import (
    STRIKES_SCHEMA,
    TRACKS_SCHEMA,
    TRUTH_SCHEMA,
    sha256_file,
    utc_iso,
    utc_now,
    write_json,
)
from earwitness.simulate.aircraft_synth import (
    AircraftScenario,
    build_tracks,
    flyover_truth,
    synthesize_aircraft,
)
from earwitness.simulate.mix import add_noise, codec_roundtrip, highpass, normalize_peak
from earwitness.simulate.thunder_synth import ThunderScenario, synthesize_thunder

FloatArray = NDArray[np.float64]

RECORDING_NAME = "recording.wav"
TRUTH_NAME = "truth.json"
STRIKES_NAME = "strikes.json"
TRACKS_NAME = "tracks.json"
PEAK = 0.5


@dataclass(frozen=True)
class MixOptions:
    """How the clean synthetic signal is degraded into a realistic recording."""

    snr_db: float | None = 10.0
    """Broadband SNR of the whole clean signal over the noise; None disables noise."""
    noise: str = "pink"
    """'pink', 'white' or 'none'."""
    codec: str | None = None
    """'aac' or 'mp3' for a lossy round trip through ffmpeg; None keeps PCM."""
    bitrate_kbps: int = 96
    highpass_hz: float | None = None
    """Device-like causal high-pass cutoff; None disables it."""


def apply_mix(
    signal: FloatArray, rate: int, mix: MixOptions, rng: np.random.Generator
) -> FloatArray:
    """Noise, then device filter, then codec, then peak normalization."""
    out = np.asarray(signal, dtype=float)
    if mix.noise != "none" and mix.snr_db is not None:
        out = add_noise(out, mix.snr_db, rng, mix.noise)
    if mix.highpass_hz:
        out = highpass(out, rate, mix.highpass_hz)
    if mix.codec:
        out = codec_roundtrip(out, rate, mix.codec, mix.bitrate_kbps)
    return normalize_peak(out, PEAK)


def _recording_record(path: Path, rate: int, n_samples: int) -> dict[str, object]:
    return {
        "file": path.name,
        "sha256": sha256_file(path),
        "sample_rate_hz": rate,
        "duration_s": n_samples / rate,
        "channels": 1,
        "format": "PCM_16",
    }


def _base_truth(
    method: str,
    observer_lat: float,
    observer_lon: float,
    observer_elevation_m: float,
    start_time: datetime,
    seed: int | None,
    mix: MixOptions,
) -> dict[str, object]:
    return {
        "schema": TRUTH_SCHEMA,
        "earwitness_version": __version__,
        "method": method,
        "created_utc": utc_iso(utc_now()),
        "seed": seed,
        "observer": {"lat": observer_lat, "lon": observer_lon, "elevation_m": observer_elevation_m},
        "start_time_utc": utc_iso(start_time, milliseconds=True),
        "start_time_unix": start_time.timestamp(),
        "mix": asdict(mix),
    }


def write_thunder_case(
    out_dir: str | Path,
    scenario: ThunderScenario,
    rate: int,
    mix: MixOptions,
    seed: int | None,
    rng: np.random.Generator,
) -> dict[str, object]:
    """Render, mix and write a thunder case folder; returns the truth record."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    clean = synthesize_thunder(scenario, rate, rng)
    mixed = apply_mix(clean, rate, mix, rng)
    wav = write_wav(out / RECORDING_NAME, mixed, rate)

    geom = scenario.geometry()
    t0 = scenario.start_time.timestamp()
    events = []
    strikes_out = []
    for s, horiz, slant, arr, amp, dec in zip(
        scenario.strikes,
        geom.horizontal_m,
        geom.slant_m,
        geom.arrival_s,
        geom.amplitude,
        geom.decay_s,
        strict=True,
    ):
        when = utc_iso(datetime.fromtimestamp(t0 + s.time_s, tz=UTC), milliseconds=True)
        events.append(
            {
                "kind": s.kind,
                "time_s": s.time_s,
                "time_utc": when,
                "lat": s.lat,
                "lon": s.lon,
                "horizontal_distance_m": float(horiz),
                "slant_range_m": float(slant),
                "arrival_time_s": float(arr),
                "amplitude": float(amp),
                "decay_s": float(dec),
                "heard": bool(0.0 <= arr <= scenario.duration_s),
            }
        )
        strikes_out.append({"time_utc": when, "lat": s.lat, "lon": s.lon, "kind": s.kind})

    write_json(
        out / STRIKES_NAME,
        {"schema": STRIKES_SCHEMA, "time_format": "iso8601_utc", "strikes": strikes_out},
    )
    truth = _base_truth(
        "thunder",
        scenario.observer_lat,
        scenario.observer_lon,
        scenario.observer_elevation_m,
        scenario.start_time,
        seed,
        mix,
    )
    truth["recording"] = _recording_record(wav, rate, mixed.size)
    truth["physics"] = asdict(scenario.config)
    truth["events"] = events
    truth["source_data"] = {"strikes": STRIKES_NAME}
    write_json(out / TRUTH_NAME, truth)
    return truth


def write_aircraft_case(
    out_dir: str | Path,
    scenario: AircraftScenario,
    rate: int,
    mix: MixOptions,
    seed: int | None,
    rng: np.random.Generator,
) -> dict[str, object]:
    """Render, mix and write an aircraft case folder; returns the truth record."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tracks = build_tracks(scenario)
    clean = synthesize_aircraft(scenario, rate, tracks)
    mixed = apply_mix(clean, rate, mix, rng)
    wav = write_wav(out / RECORDING_NAME, mixed, rate)

    t0 = scenario.start_time.timestamp()
    aircraft = []
    for flyover, track in zip(scenario.flyovers, tracks, strict=True):
        lat, lon, h = track.to_geodetic(
            scenario.observer_lat, scenario.observer_lon, scenario.observer_elevation_m
        )
        vel = track.velocity(track.t)
        ground_speed = np.hypot(vel[:, 0], vel[:, 1])
        heading = np.mod(np.rad2deg(np.arctan2(vel[:, 0], vel[:, 1])), 360.0)
        points = [
            {
                "t": float(t0 + tt),
                "lat": float(la),
                "lon": float(lo),
                "alt_m": float(hh),
                "gs_mps": float(gs),
                "track_deg": float(hd),
            }
            for tt, la, lo, hh, gs, hd in zip(
                track.t, lat, lon, h, ground_speed, heading, strict=True
            )
        ]
        aircraft.append(
            {
                "icao": flyover.icao,
                "type": flyover.type_code,
                "engine_class": flyover.engine_class,
                "points": points,
            }
        )
    write_json(
        out / TRACKS_NAME,
        {
            "schema": TRACKS_SCHEMA,
            "time_format": "unix_utc_seconds",
            "altitude": "ellipsoidal_m",
            "reference": {
                "lat": scenario.observer_lat,
                "lon": scenario.observer_lon,
                "elevation_m": scenario.observer_elevation_m,
            },
            "aircraft": aircraft,
        },
    )
    truth = _base_truth(
        "aircraft",
        scenario.observer_lat,
        scenario.observer_lon,
        scenario.observer_elevation_m,
        scenario.start_time,
        seed,
        mix,
    )
    truth["recording"] = _recording_record(wav, rate, mixed.size)
    truth["physics"] = {
        "speed_of_sound_mps": scenario.speed_of_sound,
        "amplitude_law": "1/r",
        "track_half_span_s": scenario.track_half_span_s,
    }
    truth["events"] = flyover_truth(scenario, tracks)
    truth["source_data"] = {"tracks": TRACKS_NAME}
    write_json(out / TRUTH_NAME, truth)
    return truth
