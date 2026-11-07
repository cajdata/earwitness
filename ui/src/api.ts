// The one place the page talks to the server. Every call carries the session token the
// launcher put in the page URL; the server refuses requests without it.

import type {
  AuditReport,
  Bounds,
  DataStatus,
  EnvelopeView,
  Estimate,
  EventsFile,
  FitResult,
  FlyoversResult,
  Health,
  HeatMap,
  Histogram,
  JobSnapshot,
  PreviewResult,
  RecordingInfo,
  RidgeResult,
  RunCard,
  RunSummary,
  SpectrogramView,
  StartInfo,
} from "./types";

const TOKEN_KEY = "earwitness.token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

/** Read the token from the URL once, keep it for the session, and hide it from the address bar. */
export function initToken(): void {
  const url = new URL(location.href);
  const token = url.searchParams.get("token");
  if (token) {
    sessionStorage.setItem(TOKEN_KEY, token);
    url.searchParams.delete("token");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
}

export function token(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

type Params = Record<string, string | number | boolean | number[] | null | undefined>;

function query(params?: Params): string {
  if (!params) return "";
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) value.forEach((v) => q.append(key, String(v)));
    else q.set(key, String(value));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function request<T>(method: string, path: string, body?: unknown, params?: Params): Promise<T> {
  const headers: Record<string, string> = { "X-Earwitness-Token": token() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`/api${path}${query(params)}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) {
    let detail = text || response.statusText;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (parsed.detail !== undefined) {
        detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
      }
    } catch {
      // not JSON; keep the raw text
    }
    throw new ApiError(response.status, detail);
  }
  return (text ? JSON.parse(text) : null) as T;
}

const get = <T>(path: string, params?: Params) => request<T>("GET", path, undefined, params);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});

export const api = {
  health: () => get<Health>("/health"),

  openRecording: (path: string) => post<RecordingInfo>("/recordings/open", { path }),
  recordings: () => get<RecordingInfo[]>("/recordings"),
  spectrogram: (rid: string, params: Params) => get<SpectrogramView>(`/recordings/${rid}/spectrogram`, params),
  envelope: (rid: string) => get<EnvelopeView>(`/recordings/${rid}/envelope`),
  ridge: (rid: string, body: Record<string, unknown>) => post<RidgeResult>(`/recordings/${rid}/ridge`, body),
  audit: (rid: string) => post<AuditReport>(`/recordings/${rid}/audit`),
  startInfo: (rid: string) => get<StartInfo>(`/recordings/${rid}/start`),
  flyovers: (rid: string) => post<FlyoversResult>(`/recordings/${rid}/flyovers`),
  startLocate: (body: Record<string, unknown>) => post<JobSnapshot>("/locate", body),

  preview: (body: Record<string, unknown>) => post<PreviewResult>("/doppler/preview", body),
  fit: (points: [number, number][]) => post<FitResult>("/doppler/fit", { points }),

  dataStatus: (date: string, bbox: Bounds, variant = "prod") =>
    get<DataStatus>("/data/status", { date, bbox, variant }),
  fetchAdsb: (date: string, bbox: Bounds, variant = "prod") =>
    post<JobSnapshot>("/data/adsb", { date, bbox, variant }),
  estimate: (body: Record<string, unknown>) => post<Estimate>("/estimate", body),

  startThunder: (body: Record<string, unknown>) => post<JobSnapshot>("/runs/thunder", body),
  startAircraft: (body: Record<string, unknown>) => post<JobSnapshot>("/runs/aircraft", body),
  job: (id: string, since = 0) => get<JobSnapshot>(`/jobs/${id}`, { since }),
  jobs: () => get<JobSnapshot[]>("/jobs"),

  runs: () => get<RunCard[]>("/runs"),
  runSummary: (id: string) => get<RunSummary>(`/runs/${id}`),
  heatmap: (id: string) => get<HeatMap>(`/runs/${id}/heatmap`),
  histogram: (id: string) => get<Histogram>(`/runs/${id}/histogram`),
  overlay: <T>(id: string, rank: number) => get<T>(`/runs/${id}/overlay/${rank}`),
  runEvents: (id: string) => get<EventsFile>(`/runs/${id}/events`),
  runLog: async (id: string): Promise<string> => {
    const response = await fetch(`/api/runs/${id}/log`, { headers: { "X-Earwitness-Token": token() } });
    if (!response.ok) throw new ApiError(response.status, await response.text());
    return response.text();
  },
};

/** Native dialogs when the page runs inside the pywebview window; null in a plain browser. */
export async function pickFile(): Promise<string | null | undefined> {
  const bridge = (window as unknown as { pywebview?: { api?: { pick_file?: () => Promise<string | null> } } }).pywebview;
  if (!bridge?.api?.pick_file) return undefined;
  return bridge.api.pick_file();
}
