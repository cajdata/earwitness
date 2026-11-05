// Shapes of what the API returns. They mirror the JSON the Python side writes; nothing here
// is computed in the browser.

export type Bounds = [number, number, number, number]; // west, south, east, north

export interface Health {
  version: string;
  ffmpeg: boolean;
  cache_root: string;
  runs_dir: string;
  ui_built: boolean;
  busy: boolean;
  recordings: number;
}

export interface RecordingInfo {
  id: string;
  path: string;
  name: string;
  sha256: string;
  duration_s: number;
  rate_hz: number;
}

export interface SpectrogramView {
  t: number[];
  f: number[];
  db: number[][];
  t_step_s: number | null;
  f_step_hz: number | null;
}

export interface EnvelopeView {
  t: number[];
  values: number[];
  wind: boolean[];
  frame_s: number;
  wind_fraction: number;
}

export interface RidgeResult {
  t: number[];
  f: number[];
  db: number[];
}

export interface PreviewResult {
  t: number[];
  curves: number[][];
}

export type ParamName = "f0_hz" | "v_mps" | "d_m" | "t0_s";

export interface FitResult {
  f0_hz: number;
  v_mps: number;
  d_m: number;
  t0_s: number;
  sigma: Record<ParamName, number | null>;
  rms_hz: number;
  n_points: number;
  ranges: Record<ParamName, [number, number]>;
  /** Speed profile rows [v_mps, d_m, f0_hz, t0_s, rms_hz] the trace cannot reject. */
  valley?: number[][];
}

export interface FlightLabel {
  icao: string;
  leg?: number;
  type_code?: string;
  engine_class?: string;
  [key: string]: unknown;
}

export interface TopEntry {
  rank: number;
  lat: number;
  lon: number;
  score: number;
  offset_utc?: string;
  n_strikes?: number;
  t_start_utc?: string;
  flights?: (FlightLabel | null)[];
  event_scores?: number[];
  f0_hz?: number[];
  silence_penalty_db?: number;
  [key: string]: unknown;
}

export interface RunResult {
  run_id: string;
  method: "thunder" | "aircraft";
  mode?: string;
  top: TopEntry[];
  runtime_s: number;
}

export interface JobSnapshot {
  id: string;
  kind: string;
  label: string;
  status: "queued" | "running" | "done" | "failed";
  lines: string[];
  next: number;
  result: (RunResult & Record<string, unknown>) | Record<string, unknown> | null;
  error: string | null;
  elapsed_s: number | null;
}

export interface RunCard {
  id: string;
  method: string;
  mode: string | null;
  recording: string | null;
  runtime_s: number | null;
  n_top: number;
  best: { lat: number; lon: number; score: number } | null;
}

export interface RunSummary {
  schema: string;
  mode?: string;
  recording: { file: string; duration_s: number; [key: string]: unknown };
  priors: { start_utc: string; end_utc: string; region: number[] | null };
  counts: Record<string, number>;
  top: TopEntry[];
  runtime_s: number;
  events?: EventsFile;
  [key: string]: unknown;
}

export interface HeatFeature {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: { stage: string; score: number; rank?: number; [key: string]: unknown };
}

export interface HeatMap {
  type: "FeatureCollection";
  features: HeatFeature[];
}

export interface Histogram {
  edges: number[];
  counts: number[];
  n?: number;
}

export interface ThunderOverlay {
  lat: number;
  lon: number;
  offset_utc: string;
  step_s: number;
  n_strikes: number;
  t_s: number[];
  observed: number[];
  modeled: number[];
}

export interface AircraftCurve {
  event: string;
  t_s: number[];
  modeled_hz: number[];
  ridge_t_s: number[];
  ridge_hz: number[];
}

export interface AircraftOverlay {
  lat: number;
  lon: number;
  curves: AircraftCurve[];
}

export interface Clue {
  kind: string;
  label: string;
  present: boolean;
  confidence: "none" | "weak" | "likely";
  summary: string;
  evidence: Record<string, unknown>;
  leaks: string;
  countermeasure: string;
}

export interface AuditReport {
  schema: string;
  file: Record<string, unknown>;
  metadata: Record<string, unknown>;
  clues: Clue[];
  any_present: boolean;
}

export interface FlyoverEvent {
  id: string;
  t0_s: [number, number];
  f0_hz: [number, number];
  v_mps: [number, number];
  d_m: [number, number];
  harmonics: number;
  trace: [number, number][] | null;
  /** Speed profile rows [v_mps, d_m, f0_hz, t0_s] from the fit, when there was one. */
  valley?: number[][] | null;
}

export interface SilenceWindow {
  start_s: number;
  end_s: number;
}

export interface EventsFile {
  schema: "earwitness.events/1";
  recording: Record<string, unknown>;
  known_time_utc: string | null;
  known_time_tolerance_s: number;
  events: FlyoverEvent[];
  silence: SilenceWindow[];
}

export interface DataStatus {
  adsb: {
    variant: string;
    regions: { bbox: number[]; path: string; covers: boolean }[];
    covered: boolean;
    types_cached: boolean;
  };
  glm: { satellite: string; hours_cached: number };
  cache_root: string;
}

export interface Estimate {
  method: string;
  area_km2: number;
  days: number;
  seconds: number;
  text: string;
  coarse_cells?: number;
  start_times?: number;
}
