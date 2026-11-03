"""Synthetic propeller flyovers: harmonic tones swept by the exact Doppler model.

Each flyover is a straight, level, constant-speed track. Its received frequency comes from the
same forward model the search will use (`earwitness.aircraft.model.doppler_curve`), so the
spectrogram ridge of the synthetic recording is the modeled curve by construction. Amplitude
follows 1/r in pressure. The tracks are also exported as ADS-B-like traces at 1 s.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import cumulative_trapezoid

from earwitness.aircraft.model import Track, closest_approach, doppler_curve, straight_track
from earwitness.core.physics import SPEED_OF_SOUND, DopplerCurve

FloatArray = NDArray[np.float64]

CHUNK = 1 << 20
ORIGIN = np.zeros(3)


@dataclass(frozen=True)
class Flyover:
    """One propeller aircraft passing the observer."""

    speed_mps: float
    height_m: float
    """Height above the observer, m."""
    heading_deg: float
    lateral_offset_m: float
    """Closest-approach horizontal offset, m, positive to the right of the heading."""
    t_cpa_s: float
    """Closest-approach (emission) time, seconds from the recording start."""
    f0_hz: float
    """Fundamental (blade-passage) frequency at rest."""
    harmonics: int = 8
    harmonic_rolloff: float = 1.0
    """Harmonic n has relative amplitude n ** -rolloff."""
    icao: str = "ad5b01"
    type_code: str = "C172"
    engine_class: str = "L1P"

    @property
    def cpa_slant_m(self) -> float:
        """Closest-approach slant distance, m."""
        return float(np.hypot(self.lateral_offset_m, self.height_m))


@dataclass
class AircraftScenario:
    """Everything needed to render an aircraft recording with a known answer."""

    observer_lat: float
    observer_lon: float
    observer_elevation_m: float
    start_time: datetime
    duration_s: float
    flyovers: list[Flyover]
    speed_of_sound: float = SPEED_OF_SOUND
    track_half_span_s: float = 600.0
    """Each exported track covers t_cpa plus or minus this many seconds."""

    def __post_init__(self) -> None:
        if self.start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware (UTC)")
        self.start_time = self.start_time.astimezone(UTC)
        if self.duration_s <= 0:
            raise ValueError("duration_s must be positive")


def build_track(flyover: Flyover, scenario: AircraftScenario, step_s: float = 1.0) -> Track:
    """Straight track for a flyover, in ENU about the observer, recording-relative seconds."""
    half = scenario.track_half_span_s
    return straight_track(
        speed_mps=flyover.speed_mps,
        heading_deg=flyover.heading_deg,
        height_m=flyover.height_m,
        lateral_offset_m=flyover.lateral_offset_m,
        t_cpa_s=flyover.t_cpa_s,
        t_start=flyover.t_cpa_s - half,
        t_end=flyover.t_cpa_s + half,
        step_s=step_s,
        icao=flyover.icao,
        type_code=flyover.type_code,
    )


def build_tracks(scenario: AircraftScenario) -> list[Track]:
    """One track per flyover."""
    return [build_track(f, scenario) for f in scenario.flyovers]


def flyover_curve(
    track: Track, flyover: Flyover, scenario: AircraftScenario, t_r: FloatArray
) -> DopplerCurve:
    """Exact Doppler curve of a flyover at the observer for the given reception times."""
    return doppler_curve(track, ORIGIN, t_r, flyover.f0_hz, c=scenario.speed_of_sound)


AMPLITUDE_REFERENCE_M = 300.0
"""A tone at this slant range has amplitude 1; amplitude falls as 1/r from there."""


def _render_tone(
    out: FloatArray,
    rate: int,
    t_lo: float,
    t_hi: float,
    curve: DopplerCurve,
    flyover: Flyover,
    reference_m: float = AMPLITUDE_REFERENCE_M,
) -> None:
    """Add the harmonic tone with instantaneous frequency `curve.f` to `out`, in place.

    `curve.t_r` is clip-relative reception time; sample k of the span sits at t_lo + k / rate.
    """
    phase_c = 2.0 * np.pi * cumulative_trapezoid(curve.f, curve.t_r, initial=0.0)
    amp_c = np.minimum(reference_m / curve.r, 1.0)
    i0 = int(np.ceil(t_lo * rate))
    i1 = min(int(np.floor(t_hi * rate)), out.size)
    f_max = float(np.max(curve.f))
    weights = [
        (n, n**-flyover.harmonic_rolloff)
        for n in range(1, flyover.harmonics + 1)
        if n * f_max < 0.45 * rate
    ]
    for s in range(i0, i1, CHUNK):
        e = min(s + CHUNK, i1)
        ts = np.arange(s, e) / rate
        phase = np.interp(ts, curve.t_r, phase_c)
        amp = np.interp(ts, curve.t_r, amp_c)
        acc = np.zeros(e - s)
        for n, w in weights:
            acc += w * np.sin(n * phase)
        out[s:e] += amp * acc


def synthesize_aircraft(
    scenario: AircraftScenario,
    rate: int,
    tracks: list[Track] | None = None,
    coarse_dt_s: float = 0.01,
) -> FloatArray:
    """Render the clean tone signal (no background noise) for a scenario.

    The Doppler curve is evaluated on a coarse grid (default 10 ms) and its phase integral is
    interpolated to the sample grid, which keeps memory flat for long clips.
    """
    tracks = tracks or build_tracks(scenario)
    n = round(scenario.duration_s * rate)
    out = np.zeros(n)
    for flyover, track in zip(scenario.flyovers, tracks, strict=True):
        t_lo = max(0.0, flyover.t_cpa_s - scenario.track_half_span_s)
        t_hi = min(scenario.duration_s, flyover.t_cpa_s + scenario.track_half_span_s)
        if t_hi <= t_lo:
            continue
        t_c = np.arange(t_lo, t_hi + coarse_dt_s, coarse_dt_s)
        curve = flyover_curve(track, flyover, scenario, t_c)
        _render_tone(out, rate, t_lo, t_hi, curve, flyover)
    return out


@dataclass(frozen=True)
class TrackSource:
    """A real or synthetic track to render: any Cartesian frame shared with the observer."""

    track: Track
    f0_hz: float
    harmonics: int = 8
    harmonic_rolloff: float = 1.0
    label: str = ""


def synthesize_from_tracks(
    observer_xyz: FloatArray,
    t_start_unix: float,
    duration_s: float,
    rate: int,
    sources: list[TrackSource],
    speed_of_sound: float = SPEED_OF_SOUND,
    coarse_dt_s: float = 0.01,
    reference_m: float = AMPLITUDE_REFERENCE_M,
) -> FloatArray:
    """Render tones for tracks with absolute times, heard at `observer_xyz` from `t_start_unix`."""
    n = round(duration_s * rate)
    out = np.zeros(n)
    for src in sources:
        a, b = src.track.span
        lo = max(0.0, a - t_start_unix + 2.0)
        hi = min(duration_s, b - t_start_unix - 2.0)
        if hi <= lo:
            continue
        t_c = np.arange(lo, hi + coarse_dt_s, coarse_dt_s)
        curve = doppler_curve(
            src.track, observer_xyz, t_start_unix + t_c, src.f0_hz, c=speed_of_sound
        )
        shifted = DopplerCurve(t_r=t_c, t_e=curve.t_e, r=curve.r, v_r=curve.v_r, f=curve.f)
        stand_in = Flyover(0.0, 0.0, 0.0, 0.0, 0.0, src.f0_hz, src.harmonics, src.harmonic_rolloff)
        _render_tone(out, rate, lo, hi, shifted, stand_in, reference_m)
    return out


def flyover_truth(scenario: AircraftScenario, tracks: list[Track]) -> list[dict[str, object]]:
    """Ground-truth records for truth.json, one per flyover."""
    records: list[dict[str, object]] = []
    for flyover, track in zip(scenario.flyovers, tracks, strict=True):
        t_cpa, slant = closest_approach(track, ORIGIN)
        heard = t_cpa + slant / scenario.speed_of_sound
        cpa_utc = datetime.fromtimestamp(scenario.start_time.timestamp() + t_cpa, tz=UTC)
        records.append(
            {
                "kind": "flyover",
                "icao": flyover.icao,
                "type": flyover.type_code,
                "engine_class": flyover.engine_class,
                "f0_hz": flyover.f0_hz,
                "harmonics": flyover.harmonics,
                "harmonic_rolloff": flyover.harmonic_rolloff,
                "speed_mps": flyover.speed_mps,
                "height_m": flyover.height_m,
                "heading_deg": flyover.heading_deg,
                "lateral_offset_m": flyover.lateral_offset_m,
                "cpa_slant_m": slant,
                "cpa_time_s": t_cpa,
                "cpa_heard_time_s": heard,
                "cpa_time_utc": cpa_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "track_span_s": [track.span[0], track.span[1]],
            }
        )
    return records
