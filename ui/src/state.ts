// Page state: what the user has chosen so far, kept in localStorage so a reload (or the next
// session) picks up where they left off. Recordings are reopened on the server by path at start.

import type { Bounds, EventsFile, FlyoverEvent, RecordingInfo } from "./types";

export type Method = "audit" | "thunder" | "aircraft";
export type ViewName = "audit" | "setup" | "audio" | "run" | "results";

export interface Priors {
  date: string;
  dateEnd: string;
  bbox: Bounds | null;
  knownTime: string;
  knownTimeTolerance: number;
}

export interface ThunderOptions {
  step_s: number;
  rmax_km: number;
  grid_km: number;
  top: number;
  refine: boolean;
  land_mask: boolean;
  bearing_weight: number;
  mask_wind: boolean;
  score: "cosine" | "pearson";
  satellite: string;
}

export interface AircraftOptions {
  silence: "hard" | "soft" | "off";
  terrain: boolean;
  top: number;
  max_candidates: number;
  harmonics: number;
  variant: "prod" | "staging";
}

export interface AppState {
  view: ViewName;
  method: Method;
  recording: RecordingInfo | null;
  priors: Priors;
  events: EventsFile;
  thunder: ThunderOptions;
  aircraft: AircraftOptions;
  jobId: string | null;
  runId: string | null;
}

const KEY = "earwitness.state.v1";

export function emptyEvents(): EventsFile {
  return {
    schema: "earwitness.events/1",
    recording: {},
    known_time_utc: null,
    known_time_tolerance_s: 1.0,
    events: [],
    silence: [],
  };
}

function defaults(): AppState {
  return {
    view: "audit",
    method: "audit",
    recording: null,
    priors: { date: "", dateEnd: "", bbox: null, knownTime: "", knownTimeTolerance: 1.0 },
    events: emptyEvents(),
    thunder: {
      step_s: 5,
      rmax_km: 25,
      grid_km: 5,
      top: 10,
      refine: true,
      land_mask: true,
      bearing_weight: 0,
      mask_wind: false,
      score: "cosine",
      satellite: "auto",
    },
    aircraft: { silence: "soft", terrain: true, top: 10, max_candidates: 30000, harmonics: 3, variant: "prod" },
    jobId: null,
    runId: null,
  };
}

function load(): AppState {
  const base = defaults();
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return base;
    const saved = JSON.parse(raw) as Partial<AppState>;
    return {
      ...base,
      ...saved,
      priors: { ...base.priors, ...(saved.priors ?? {}) },
      thunder: { ...base.thunder, ...(saved.thunder ?? {}) },
      aircraft: { ...base.aircraft, ...(saved.aircraft ?? {}) },
      events: saved.events ?? base.events,
    };
  } catch {
    return base;
  }
}

export const state: AppState = load();

export function save(): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // storage may be unavailable; the page still works for the session
  }
}

export function resetState(): void {
  Object.assign(state, defaults());
  save();
}

/** A fresh event with sensible ranges around a heard closest-approach time. */
export function newEvent(t0: number, f0 = 100): FlyoverEvent {
  const n = state.events.events.length + 1;
  return {
    id: `e${n}`,
    t0_s: [Math.max(0, t0 - 3), t0 + 3],
    f0_hz: [Math.max(10, f0 - 3), f0 + 3],
    v_mps: [40, 110],
    d_m: [100, 1500],
    harmonics: 3,
    trace: null,
  };
}

// A tiny event bus so views can react to each other's changes without knowing about each other.
const bus = new EventTarget();

export function emit(name: string): void {
  bus.dispatchEvent(new Event(name));
}

export function on(name: string, handler: () => void): void {
  bus.addEventListener(name, handler);
}
