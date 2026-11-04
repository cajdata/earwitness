// Results view: the heat map, the ranked list, the score histogram, and the observed versus
// modeled overlay for the selected candidate. Any run folder can be reopened here.

import type maplibregl from "maplibre-gl";
import { api } from "../api";
import { button, clear, fmtDuration, h, notice, pill, select } from "../dom";
import { boundsOf, createMap, fitBounds, marker, showHeat } from "../map";
import * as plots from "../plots";
import { save, state } from "../state";
import type { AircraftOverlay, FlightLabel, RunCard, RunSummary, ThunderOverlay, TopEntry } from "../types";

export function mountResults(container: HTMLElement): () => void {
  const picker = h("div", { class: "row" });
  const header = h("div");
  const mapEl = h("div", { class: "map" });
  const listBox = h("div");
  const overlayPlot = h("div", { class: "plot" });
  const overlayNote = h("p", { class: "muted" });
  const histPlot = h("div", { class: "plot", style: { minHeight: "240px" } });
  const logBox = h("details", {}, h("summary", {}, "run log"), h("pre", { class: "log" }));
  let map: maplibregl.Map | null = null;
  let pin: maplibregl.Marker | null = null;
  let summary: RunSummary | null = null;
  let runId: string | null = state.runId;

  container.append(
    h("div", { class: "card" }, h("h2", {}, "Results"), picker, header),
    h(
      "div",
      { class: "grid wide", style: { marginTop: "16px" } },
      h("div", { class: "card" }, mapEl, h("p", { class: "muted" }, "Coloured points are scored cells (blue low, red high); numbered markers are the ranked answers. Click a row to inspect a candidate.")),
      h("div", { class: "card" }, h("h2", {}, "Ranked candidates"), listBox),
    ),
    h(
      "div",
      { class: "grid", style: { marginTop: "16px" } },
      h("div", { class: "card" }, h("h2", {}, "Observed versus modeled"), overlayNote, overlayPlot),
      h("div", { class: "card" }, h("h2", {}, "Score histogram"), histPlot, logBox),
    ),
  );

  requestAnimationFrame(() => {
    if (!map && mapEl.isConnected) map = createMap(mapEl);
    void loadRuns();
  });

  async function loadRuns(): Promise<void> {
    let cards: RunCard[] = [];
    try {
      cards = await api.runs();
    } catch (err) {
      picker.replaceChildren(notice("error", (err as Error).message));
      return;
    }
    if (!cards.length) {
      picker.replaceChildren(h("p", { class: "muted" }, "No runs yet. Start one from the Run page."));
      return;
    }
    if (!runId || !cards.some((c) => c.id === runId)) runId = cards[0].id;
    const options = cards.map((c) => [c.id, `${c.id}  (${c.method}${c.mode ? ", " + c.mode : ""}${c.recording ? ", " + c.recording : ""})`] as [string, string]);
    picker.replaceChildren(
      select(options, runId, (v) => {
        runId = v;
        state.runId = v;
        save();
        void loadRun();
      }),
      button("Refresh", () => void loadRuns(), "btn small"),
    );
    await loadRun();
  }

  async function loadRun(): Promise<void> {
    if (!runId) return;
    try {
      summary = await api.runSummary(runId);
    } catch (err) {
      header.replaceChildren(notice("error", (err as Error).message));
      return;
    }
    const method = summary.schema.includes("thunder") ? "thunder" : "aircraft";
    const top = summary.top ?? [];
    header.replaceChildren(
      h(
        "div",
        {},
      h(
        "p",
        {},
        pill(method, "accent"),
        summary.mode ? pill(summary.mode, "") : null,
        ` ${summary.recording.file} · priors ${summary.priors.start_utc} to ${summary.priors.end_utc}` +
          (summary.priors.region ? ` over ${summary.priors.region.map((x) => x.toFixed(2)).join(", ")}` : "") +
          ` · ${fmtDuration(summary.runtime_s)} · ` +
          Object.entries(summary.counts ?? {}).map(([k, v]) => `${k} ${v}`).join(", "),
      ),
      top.length >= 2 && method === "aircraft" && summary.mode === "single-event"
        ? notice("warn", "Single-event mode: one straight flyover cannot tell the two sides of the track apart, so the mirror site scores the same. Read the whole list, not just rank 1.")
        : null,
      ),
    );
    renderList(method, top);
    try {
      const heat = await api.heatmap(runId);
      if (map) {
        showHeat(map, heat);
        const b = boundsOf(heat);
        if (b) fitBounds(map, b);
      }
    } catch {
      // some runs have no heat map (coarse pass only)
    }
    try {
      const hist = await api.histogram(runId);
      await plots.draw(histPlot, plots.histogramTraces(hist), plots.baseLayout({ xaxis: { title: { text: method === "thunder" ? "cosine score" : "score (dB)" } }, yaxis: { title: { text: "candidates" } }, height: 240 }));
    } catch {
      plots.purge(histPlot);
    }
    try {
      const text = await api.runLog(runId);
      (logBox.querySelector("pre") as HTMLElement).textContent = text;
    } catch {
      (logBox.querySelector("pre") as HTMLElement).textContent = "(no log)";
    }
    if (top.length) await selectRank(method, top[0]);
  }

  function renderList(method: "thunder" | "aircraft", top: TopEntry[]): void {
    clear(listBox);
    if (!top.length) {
      listBox.append(h("p", { class: "muted" }, "This run produced no ranked candidates."));
      return;
    }
    const headers = method === "thunder" ? ["#", "score", "margin", "lat", "lon", "clip start (UTC)", "strikes"] : ["#", "score dB", "margin", "lat", "lon", "clip start (UTC)", "flights", "events"];
    const rows = top.map((t, i) => {
      const next = top[i + 1];
      const margin = next ? (t.score - next.score).toFixed(method === "thunder" ? 3 : 2) : "";
      const cells: (string | Node)[] =
        method === "thunder"
          ? [String(t.rank), t.score.toFixed(3), margin, t.lat.toFixed(4), t.lon.toFixed(4), t.offset_utc ?? "", String(t.n_strikes ?? "")]
          : [
              String(t.rank),
              t.score.toFixed(2),
              margin,
              t.lat.toFixed(4),
              t.lon.toFixed(4),
              t.t_start_utc ?? "",
              (t.flights ?? [])
                .filter((f): f is FlightLabel => Boolean(f && f.icao))
                .map((f) => `${f.icao}${f.type_code ? " " + f.type_code : ""}`)
                .join(" + "),
              (t.event_scores ?? []).map((s) => s.toFixed(1)).join(" / ") + (t.silence_penalty_db ? ` (−${t.silence_penalty_db.toFixed(0)} dB silence)` : ""),
            ];
      const row = h("tr", { class: "selectable", onclick: () => void selectRank(method, t) }, ...cells.map((c) => h("td", {}, c)));
      row.dataset.rank = String(t.rank);
      return row;
    });
    listBox.append(h("table", { class: "table" }, h("thead", {}, h("tr", {}, ...headers.map((x) => h("th", {}, x)))), h("tbody", {}, ...rows)));
  }

  async function selectRank(method: "thunder" | "aircraft", entry: TopEntry): Promise<void> {
    listBox.querySelectorAll("tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.rank === String(entry.rank)));
    if (map) {
      pin?.remove();
      pin = marker(map, entry.lon, entry.lat);
      map.flyTo({ center: [entry.lon, entry.lat], zoom: Math.max(map.getZoom(), method === "thunder" ? 9 : 13), duration: 600 });
    }
    if (!runId) return;
    try {
      if (method === "thunder") {
        const ov = await api.overlay<ThunderOverlay>(runId, entry.rank);
        overlayNote.textContent = `rank ${entry.rank}: ${ov.n_strikes} strikes within range; clip start ${ov.offset_utc}; the modeled envelope is scaled to the observed peak.`;
        await plots.draw(
          overlayPlot,
          [
            { type: "scatter", mode: "lines", x: ov.t_s, y: ov.observed, name: "observed", line: { color: "#1d4ed8", width: 1.5 } },
            { type: "scatter", mode: "lines", x: ov.t_s, y: ov.modeled, name: "modeled", line: { color: "#f97316", width: 1.5 } },
          ],
          plots.baseLayout({ showlegend: true, legend: { orientation: "h" }, xaxis: { title: { text: "seconds into the recording" } }, yaxis: { title: { text: "envelope" }, rangemode: "tozero" }, height: 300 }),
        );
      } else {
        const ov = await api.overlay<AircraftOverlay>(runId, entry.rank);
        const colors = ["#1d4ed8", "#16a34a", "#9333ea"];
        const traces: plots.Trace[] = [];
        ov.curves.forEach((c, i) => {
          traces.push({ type: "scatter", mode: "markers", x: c.ridge_t_s, y: c.ridge_hz, name: `${c.event} ridge`, marker: { color: colors[i % 3], size: 4, opacity: 0.6 } });
          traces.push({ type: "scatter", mode: "lines", x: c.t_s, y: c.modeled_hz, name: `${c.event} modeled`, line: { color: "#f97316", width: 2 } });
        });
        overlayNote.textContent = `rank ${entry.rank}: the spectrogram ridge inside each event's band against the Doppler curve this candidate predicts for the matched flight.`;
        await plots.draw(
          overlayPlot,
          traces,
          plots.baseLayout({ showlegend: true, legend: { orientation: "h" }, xaxis: { title: { text: "seconds into the recording" } }, yaxis: { title: { text: "Hz" } }, height: 300 }),
        );
      }
    } catch (err) {
      overlayNote.textContent = `no overlay for rank ${entry.rank} (${(err as Error).message})`;
      plots.purge(overlayPlot);
    }
  }

  return () => {
    plots.purge(overlayPlot);
    plots.purge(histPlot);
    map?.remove();
    map = null;
  };
}
