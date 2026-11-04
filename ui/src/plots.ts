// Plotly helpers for the spectrogram, envelope, histogram and overlay panels.

import Plotly from "plotly.js-dist-min";
import type { Histogram, SpectrogramView } from "./types";

export type Trace = Partial<Plotly.PlotData>;
export type Layout = Partial<Plotly.Layout>;

export const PLOT_CONFIG: Partial<Plotly.Config> = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ["lasso2d", "toImage", "autoScale2d"],
  scrollZoom: true,
};

const FONT = { family: "system-ui, sans-serif", size: 12 };

export function baseLayout(extra: Layout = {}): Layout {
  return {
    margin: { l: 56, r: 16, t: 24, b: 44 },
    font: FONT,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "#ffffff",
    hovermode: "closest",
    showlegend: false,
    ...extra,
  };
}

export function heatmapTrace(view: SpectrogramView, zmin = -4, zmax = 24): Trace {
  return {
    type: "heatmap",
    x: view.t,
    y: view.f,
    z: view.db,
    zmin,
    zmax,
    colorscale: "Viridis",
    colorbar: { title: { text: "dB over median" }, thickness: 12, len: 0.9 },
    hovertemplate: "%{x:.1f} s, %{y:.1f} Hz: %{z:.1f} dB<extra></extra>",
    zsmooth: false,
  };
}

export async function draw(el: HTMLElement, traces: Trace[], layout: Layout): Promise<void> {
  await Plotly.react(el, traces as Plotly.Data[], layout, PLOT_CONFIG);
}

export function histogramTraces(hist: Histogram): Trace[] {
  const centers = hist.edges.slice(0, -1).map((e, i) => 0.5 * (e + hist.edges[i + 1]));
  const width = hist.edges.length > 1 ? hist.edges[1] - hist.edges[0] : 1;
  return [
    {
      type: "bar",
      x: centers,
      y: hist.counts,
      width: Array(centers.length).fill(width * 0.95),
      marker: { color: "#3b82f6" },
      hovertemplate: "score %{x:.3f}: %{y} candidates<extra></extra>",
    },
  ];
}

/** Attach a Plotly event handler (the typings do not expose `.on` on plain elements). */
export function on(el: HTMLElement, event: string, handler: (data: unknown) => void): void {
  (el as unknown as { on: (e: string, h: (d: unknown) => void) => void }).on(event, handler);
}

export function purge(el: HTMLElement): void {
  Plotly.purge(el);
}

export function relayout(el: HTMLElement, update: Layout): void {
  void Plotly.relayout(el, update);
}

export interface RelayoutRange {
  "xaxis.range[0]"?: number;
  "xaxis.range[1]"?: number;
  "yaxis.range[0]"?: number;
  "yaxis.range[1]"?: number;
  "xaxis.autorange"?: boolean;
  "yaxis.autorange"?: boolean;
}

export interface SelectedRange {
  range?: { x: [number, number]; y: [number, number] };
}

export interface ClickEvent {
  points?: { x: number; y: number; z?: number }[];
}
