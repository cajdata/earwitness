// Audio view. Aircraft: spectrogram with band zoom, the Doppler fit sliders, the trace tool,
// events and silence windows. Thunder: the loudness envelope the search compares against.

import { api } from "../api";
import { button, clear, debounce, download, field, fmtClock, h, notice, numberInput, pill } from "../dom";
import * as plots from "../plots";
import { emit, newEvent, save, state } from "../state";
import type { EnvelopeView, EventsFile, FitResult, FlyoverEvent, PreviewResult, SpectrogramView } from "../types";

type Shapes = NonNullable<plots.Layout["shapes"]>;

function go(view: string): void {
  location.hash = `#/${view}`;
}

export function mountAudio(container: HTMLElement): () => void {
  return state.method === "thunder" ? mountThunder(container) : mountAircraft(container);
}

// ------------------------------------------------------------------------------------ thunder

function windShapes(env: EnvelopeView): Shapes {
  const shapes: Shapes = [];
  let start: number | null = null;
  env.wind.forEach((flag, i) => {
    if (flag && start === null) start = env.t[i];
    if ((!flag || i === env.wind.length - 1) && start !== null) {
      shapes.push({
        type: "rect",
        xref: "x",
        yref: "paper",
        x0: start,
        x1: env.t[i],
        y0: 0,
        y1: 1,
        fillcolor: "rgba(245, 158, 11, 0.18)",
        line: { width: 0 },
      });
      start = null;
    }
  });
  return shapes;
}

function mountThunder(container: HTMLElement): () => void {
  const rec = state.recording!;
  const plot = h("div", { class: "plot tall" });
  const info = h("p", { class: "muted" }, "Loading the envelope…");
  container.append(
    h(
      "div",
      { class: "card" },
      h("h2", {}, `Loudness envelope, 20 to 250 Hz: ${rec.name}`),
      info,
      plot,
      h(
        "p",
        { class: "muted" },
        "Thunder claps are the bursts above the baseline. The search slides the lightning record's arrival pattern along this envelope for every candidate location and start time. ",
        "Shaded stretches are flagged as wind (low band dominating); the run option “mask wind” zeroes them.",
      ),
      h("div", { class: "row" }, button("Continue to run", () => go("run"), "btn primary")),
    ),
  );
  void (async () => {
    try {
      const env = await api.envelope(rec.id);
      await plots.draw(
        plot,
        [
          {
            type: "scatter",
            mode: "lines",
            x: env.t,
            y: env.values,
            line: { color: "#1d4ed8", width: 1 },
            hovertemplate: "%{x:.1f} s: %{y:.4f}<extra></extra>",
          },
        ],
        plots.baseLayout({
          xaxis: { title: { text: "seconds into the recording" } },
          yaxis: { title: { text: "band RMS, smoothed" }, rangemode: "tozero" },
          shapes: windShapes(env),
          height: 440,
        }),
      );
      info.textContent = `${fmtClock(rec.duration_s)} clip; wind flagged on ${(100 * env.wind_fraction).toFixed(0)}% of frames.`;
    } catch (err) {
      info.replaceChildren(notice("error", (err as Error).message));
    }
  })();
  return () => plots.purge(plot);
}

// ----------------------------------------------------------------------------------- aircraft

interface Params {
  f0: number;
  v: number;
  d: number;
  t0: number;
}

const SLIDERS: { key: keyof Params; label: string; min: number; max: number; step: number; unit: string }[] = [
  { key: "f0", label: "f0", min: 10, max: 400, step: 0.5, unit: "Hz" },
  { key: "v", label: "speed", min: 5, max: 160, step: 0.5, unit: "m/s" },
  { key: "d", label: "distance", min: 20, max: 5000, step: 5, unit: "m" },
  { key: "t0", label: "t0", min: 0, max: 1, step: 0.1, unit: "s" },
];

function mountAircraft(container: HTMLElement): () => void {
  const rec = state.recording!;
  const duration = rec.duration_s;
  let spec: SpectrogramView | null = null;
  let preview: PreviewResult | null = null;
  let fitResult: FitResult | null = null;
  let trace: [number, number][] = [];
  let selected: number | null = null;
  let mode: "zoom" | "trace" | "silence" = "zoom";
  const view = { tLo: 0, tHi: duration, fLo: 0, fHi: 400 };
  const params: Params = { f0: 100, v: 70, d: 400, t0: Math.round(duration / 2) };
  let harmonics = 3;

  const plot = h("div", { class: "plot tall" });
  const status = h("div");
  const fitBox = h("div");
  const eventsBox = h("div");
  const silenceBox = h("div");

  // ------------------------------------------------------------ spectrogram and its controls
  const fLoIn = numberInput(view.fLo, (v) => setBand(v, view.fHi), { min: 0, max: 1990, width: "80px" });
  const fHiIn = numberInput(view.fHi, (v) => setBand(view.fLo, v), { min: 10, max: 2000, width: "80px" });
  const tLoIn = numberInput(view.tLo, (v) => setTime(v, view.tHi), { min: 0, max: duration, width: "90px" });
  const tHiIn = numberInput(view.tHi, (v) => setTime(view.tLo, v), { min: 0, max: duration, width: "90px" });

  function syncInputs(): void {
    fLoIn.value = view.fLo.toFixed(1);
    fHiIn.value = view.fHi.toFixed(1);
    tLoIn.value = view.tLo.toFixed(1);
    tHiIn.value = view.tHi.toFixed(1);
  }

  function setBand(lo: number, hi: number): void {
    if (hi - lo < 5) return;
    view.fLo = Math.max(0, lo);
    view.fHi = Math.min(2000, hi);
    void load();
  }

  function setTime(lo: number, hi: number): void {
    if (hi - lo < 1) return;
    view.tLo = Math.max(0, lo);
    view.tHi = Math.min(duration, hi);
    void load();
  }

  const load = async (): Promise<void> => {
    syncInputs();
    status.replaceChildren(h("span", { class: "muted" }, "loading…"));
    try {
      const narrow = view.fHi - view.fLo < 150;
      spec = await api.spectrogram(rec.id, {
        t_lo: view.tLo,
        t_hi: view.tHi,
        f_lo: view.fLo,
        f_hi: view.fHi,
        fmax: Math.max(view.fHi * 1.02, 50),
        nfft: narrow ? 4096 : 2048,
        hop: narrow ? 1024 : 512,
        max_cols: 1400,
        max_rows: 320,
      });
      status.replaceChildren();
      updatePreview();
      await redraw();
    } catch (err) {
      status.replaceChildren(notice("error", (err as Error).message));
    }
  };

  const updatePreview = debounce(() => {
    void (async () => {
      try {
        preview = await api.preview({
          ...params,
          t_lo: view.tLo,
          t_hi: view.tHi,
          step_s: Math.max((view.tHi - view.tLo) / 900, 0.05),
          harmonics,
        });
        await redraw();
      } catch {
        preview = null;
      }
    })();
  }, 80);

  function eventShapes(): Shapes {
    const shapes: Shapes = [];
    for (const w of state.events.silence) {
      shapes.push({
        type: "rect",
        xref: "x",
        yref: "paper",
        x0: w.start_s,
        x1: w.end_s,
        y0: 0,
        y1: 1,
        fillcolor: "rgba(148, 163, 184, 0.35)",
        line: { width: 0 },
      });
    }
    state.events.events.forEach((ev, i) => {
      shapes.push({
        type: "rect",
        xref: "x",
        yref: "paper",
        x0: ev.t0_s[0],
        x1: ev.t0_s[1],
        y0: 0,
        y1: 1,
        fillcolor: i === selected ? "rgba(22, 163, 74, 0.25)" : "rgba(22, 163, 74, 0.12)",
        line: { width: 1, color: "#16a34a" },
      });
    });
    return shapes;
  }

  async function redraw(): Promise<void> {
    if (!spec) return;
    const traces: plots.Trace[] = [plots.heatmapTrace(spec)];
    if (preview) {
      preview.curves.forEach((curve, n) => {
        traces.push({
          type: "scatter",
          mode: "lines",
          x: preview!.t,
          y: curve,
          line: { color: n === 0 ? "#f97316" : "rgba(249, 115, 22, 0.55)", width: n === 0 ? 2 : 1, dash: n === 0 ? "solid" : "dot" },
          hoverinfo: "skip",
          name: `model h${n + 1}`,
        });
      });
    }
    if (trace.length) {
      traces.push({
        type: "scatter",
        mode: "markers",
        x: trace.map((p) => p[0]),
        y: trace.map((p) => p[1]),
        marker: { color: "#ffffff", size: 5, line: { color: "#111827", width: 1 } },
        hovertemplate: "trace %{x:.2f} s, %{y:.2f} Hz<extra></extra>",
        name: "trace",
      });
    }
    const annotations = state.events.events.map((ev) => ({
      x: (ev.t0_s[0] + ev.t0_s[1]) / 2,
      y: 1,
      xref: "x" as const,
      yref: "paper" as const,
      text: ev.id,
      showarrow: false,
      yanchor: "bottom" as const,
      font: { size: 11, color: "#16a34a" },
    }));
    await plots.draw(
      plot,
      traces,
      plots.baseLayout({
        xaxis: { title: { text: "seconds into the recording" }, range: [view.tLo, view.tHi] },
        yaxis: { title: { text: "Hz" }, range: [view.fLo, view.fHi] },
        dragmode: mode === "zoom" ? "zoom" : mode === "silence" ? "select" : "pan",
        selectdirection: "h",
        shapes: eventShapes(),
        annotations,
        height: 480,
        uirevision: `${view.tLo}-${view.tHi}-${view.fLo}-${view.fHi}`,
      }),
    );
    attachHandlers();
  }

  const onRelayout = debounce((data: unknown) => {
    const d = data as plots.RelayoutRange;
    let changed = false;
    if (d["xaxis.range[0]"] !== undefined && d["xaxis.range[1]"] !== undefined) {
      view.tLo = Math.max(0, d["xaxis.range[0]"]);
      view.tHi = Math.min(duration, d["xaxis.range[1]"]);
      changed = true;
    }
    if (d["yaxis.range[0]"] !== undefined && d["yaxis.range[1]"] !== undefined) {
      view.fLo = Math.max(0, d["yaxis.range[0]"]);
      view.fHi = Math.min(2000, d["yaxis.range[1]"]);
      changed = true;
    }
    if (d["xaxis.autorange"] || d["yaxis.autorange"]) {
      view.tLo = 0;
      view.tHi = duration;
      view.fLo = 0;
      view.fHi = 400;
      changed = true;
    }
    if (changed) void load();
  }, 250);

  // Plotly adds `.on` to the element on the first plot, so the handlers attach after that.
  let handlersAttached = false;
  function attachHandlers(): void {
    if (handlersAttached) return;
    handlersAttached = true;
    plots.on(plot, "plotly_relayout", onRelayout);
    plots.on(plot, "plotly_click", (data) => {
      if (mode !== "trace") return;
      const p = (data as plots.ClickEvent).points?.[0];
      if (!p) return;
      trace.push([Number(p.x), Number(p.y)]);
      trace.sort((a, b) => a[0] - b[0]);
      renderFit();
      void redraw();
    });
    plots.on(plot, "plotly_selected", (data) => {
      if (mode !== "silence") return;
      const range = (data as plots.SelectedRange | undefined)?.range;
      if (!range) return;
      const [a, b] = range.x;
      addSilence(Math.max(0, Math.min(a, b)), Math.min(duration, Math.max(a, b)));
    });
  }

  const modeButtons = h("div", { class: "row" });
  function renderModes(): void {
    modeButtons.replaceChildren(
      h("span", { class: "muted" }, "Mouse:"),
      button("Zoom", () => setMode("zoom"), `btn small ${mode === "zoom" ? "toggled" : ""}`),
      button("Click to add trace points", () => setMode("trace"), `btn small ${mode === "trace" ? "toggled" : ""}`),
      button("Drag to mark silence", () => setMode("silence"), `btn small ${mode === "silence" ? "toggled" : ""}`),
      h("span", { class: "muted" }, "Scroll to zoom; double-click resets the view."),
    );
  }
  function setMode(m: typeof mode): void {
    mode = m;
    renderModes();
    void redraw();
  }
  renderModes();

  // ------------------------------------------------------------------------- fit tools
  function renderFit(): void {
    clear(fitBox);
    const grid = h("div", { class: "sliders" });
    for (const s of SLIDERS) {
      const max = s.key === "t0" ? duration : s.max;
      const range = h("input", { type: "range", min: s.min, max, step: s.step, value: params[s.key] });
      const num = numberInput(params[s.key], (v) => setParam(s.key, v), { min: s.min, max, step: s.step, width: "90px" });
      range.addEventListener("input", () => {
        params[s.key] = Number(range.value);
        num.value = range.value;
        updatePreview();
      });
      grid.append(h("span", {}, `${s.label} (${s.unit})`), range, num);
    }
    const harmonicsIn = numberInput(harmonics, (v) => {
      harmonics = Math.max(1, Math.min(12, Math.round(v)));
      updatePreview();
    }, { min: 1, max: 12, step: 1, width: "70px" });
    fitBox.append(
      h("p", { class: "muted" }, "Move the sliders until the orange curve sits on the sweeping line. t0 is the heard closest approach, where the tone crosses f0."),
      grid,
      h("div", { class: "row" }, field("harmonics drawn and scored", harmonicsIn)),
      h("h3", {}, "Trace"),
      h(
        "div",
        { class: "row" },
        button("Auto-trace the view", () => void autoTrace(), "btn small"),
        button(`Clear (${trace.length} points)`, () => {
          trace = [];
          renderFit();
          void redraw();
        }, "btn small"),
        button("Fit from trace", () => void fitTrace(), `btn small ${trace.length >= 5 ? "primary" : ""}`),
      ),
      fitResult
        ? h(
            "p",
            { class: "mono muted" },
            `fit rms ${fitResult.rms_hz.toFixed(2)} Hz over ${fitResult.n_points} points · ` +
              `f0 ${fitResult.ranges.f0_hz.map((x) => x.toFixed(1)).join("–")} Hz · v ${fitResult.ranges.v_mps.map((x) => x.toFixed(0)).join("–")} m/s · ` +
              `d ${fitResult.ranges.d_m.map((x) => x.toFixed(0)).join("–")} m · t0 ${fitResult.ranges.t0_s.map((x) => x.toFixed(1)).join("–")} s`,
          )
        : h("p", { class: "muted" }, "Auto-trace follows the brightest line in the current view; add or remove points by clicking, then fit."),
      h(
        "div",
        { class: "row" },
        button(selected === null ? "Add event from this fit" : `Update ${state.events.events[selected]?.id ?? "event"} from this fit`, () => saveEvent(), "btn primary"),
        selected !== null ? button("New event instead", () => {
          selected = null;
          renderFit();
          renderEvents();
        }, "btn small") : null,
      ),
    );
  }

  function setParam(key: keyof Params, value: number): void {
    params[key] = value;
    renderFit();
    updatePreview();
  }

  async function autoTrace(): Promise<void> {
    try {
      const r = await api.ridge(rec.id, { f_lo: view.fLo, f_hi: view.fHi, t_lo: view.tLo, t_hi: view.tHi, min_db: 6 });
      const pts: [number, number][] = r.t.map((t, i) => [t, r.f[i]]);
      const step = Math.max(1, Math.ceil(pts.length / 600));
      trace = pts.filter((_, i) => i % step === 0);
      fitResult = null;
      renderFit();
      await redraw();
      if (trace.length < 5) status.replaceChildren(notice("warn", "Fewer than 5 bright frames in this view; zoom onto the sweep or lower the band."));
    } catch (err) {
      status.replaceChildren(notice("error", (err as Error).message));
    }
  }

  async function fitTrace(): Promise<void> {
    if (trace.length < 5) return;
    try {
      fitResult = await api.fit(trace);
      params.f0 = fitResult.f0_hz;
      params.v = fitResult.v_mps;
      params.d = fitResult.d_m;
      params.t0 = fitResult.t0_s;
      renderFit();
      updatePreview();
    } catch (err) {
      status.replaceChildren(notice("error", (err as Error).message));
    }
  }

  function rangesFromState(): Pick<FlyoverEvent, "t0_s" | "f0_hz" | "v_mps" | "d_m"> {
    if (fitResult) return fitResult.ranges;
    return {
      // the event's t0 is the heard crossing; the preview's t0 is the closest approach itself
      t0_s: [Math.max(0, params.t0 + params.d / 343 - 3), params.t0 + params.d / 343 + 3],
      f0_hz: [Math.max(10, params.f0 - 3), params.f0 + 3],
      v_mps: [Math.max(5, params.v * 0.8), params.v * 1.2],
      d_m: [Math.max(20, params.d * 0.5), params.d * 1.5],
    };
  }

  function saveEvent(): void {
    const ranges = rangesFromState();
    const tracePts = trace.length ? trace.map((p) => [Math.round(p[0] * 100) / 100, Math.round(p[1] * 100) / 100] as [number, number]) : null;
    if (selected === null) {
      const ev = newEvent(params.t0, params.f0);
      Object.assign(ev, ranges, { harmonics, trace: tracePts, valley: fitResult?.valley ?? null });
      state.events.events.push(ev);
      selected = state.events.events.length - 1;
    } else {
      Object.assign(state.events.events[selected], ranges, { harmonics, trace: tracePts, valley: fitResult?.valley ?? null });
    }
    state.events.events.sort((a, b) => a.t0_s[0] - b.t0_s[0]);
    state.events.events.forEach((ev, i) => (ev.id = `e${i + 1}`));
    save();
    emit("events");
    renderEvents();
    renderFit();
    void redraw();
  }

  // ------------------------------------------------------------------------- events list
  function rangeCell(ev: FlyoverEvent, key: "t0_s" | "f0_hz" | "v_mps" | "d_m", digits: number): HTMLElement {
    const lo = numberInput(ev[key][0], (v) => {
      ev[key][0] = v;
      save();
      void redraw();
    }, { width: "72px", step: digits ? 0.1 : 1 });
    const hi = numberInput(ev[key][1], (v) => {
      ev[key][1] = v;
      save();
      void redraw();
    }, { width: "72px", step: digits ? 0.1 : 1 });
    return h("span", { class: "row", style: { margin: "0", gap: "4px" } }, lo, "–", hi);
  }

  function renderEvents(): void {
    clear(eventsBox);
    const evs = state.events.events;
    if (!evs.length) {
      eventsBox.append(h("p", { class: "muted" }, "No events yet. Fit a flyover, then add it. Two flyovers are needed, or one plus a known start time (set in Setup)."));
    } else {
      const rows = evs.map((ev, i) =>
        h(
          "tr",
          { class: `selectable ${i === selected ? "selected" : ""}` },
          h("td", { onclick: () => selectEvent(i) }, h("strong", {}, ev.id)),
          h("td", {}, rangeCell(ev, "t0_s", 1)),
          h("td", {}, rangeCell(ev, "f0_hz", 1)),
          h("td", {}, rangeCell(ev, "v_mps", 0)),
          h("td", {}, rangeCell(ev, "d_m", 0)),
          h(
            "td",
            {},
            numberInput(ev.harmonics, (v) => {
              ev.harmonics = Math.max(1, Math.round(v));
              save();
            }, { min: 1, max: 12, step: 1, width: "60px" }),
          ),
          h("td", { class: "muted" }, ev.trace ? `${ev.trace.length} pts` : "none"),
          h(
            "td",
            {},
            button("Load", () => selectEvent(i), "btn small"),
            " ",
            button("Delete", () => {
              evs.splice(i, 1);
              evs.forEach((e, k) => (e.id = `e${k + 1}`));
              selected = null;
              save();
              emit("events");
              renderEvents();
              renderFit();
              void redraw();
            }, "btn small danger"),
          ),
        ),
      );
      eventsBox.append(
        h(
          "table",
          { class: "table" },
          h("thead", {}, h("tr", {}, ...["event", "t0 (s)", "f0 (Hz)", "speed (m/s)", "distance (m)", "harm.", "trace", ""].map((t) => h("th", {}, t)))),
          h("tbody", {}, ...rows),
        ),
      );
    }
    const known = state.priors.knownTime;
    eventsBox.append(
      h(
        "p",
        { class: "muted" },
        evs.length >= 2
          ? `${evs.length} events: two-event mode locks the recording start from their spacing.`
          : known
            ? `One event with the known start time ${known}: single-event mode.`
            : "Single-event mode needs the recording's start time (Setup).",
      ),
    );
  }

  function selectEvent(i: number): void {
    const ev = state.events.events[i];
    selected = i;
    params.t0 = (ev.t0_s[0] + ev.t0_s[1]) / 2 - (ev.d_m[0] + ev.d_m[1]) / 2 / 343;
    params.f0 = (ev.f0_hz[0] + ev.f0_hz[1]) / 2;
    params.v = (ev.v_mps[0] + ev.v_mps[1]) / 2;
    params.d = (ev.d_m[0] + ev.d_m[1]) / 2;
    harmonics = ev.harmonics;
    trace = ev.trace ? ev.trace.map((p) => [p[0], p[1]]) : [];
    fitResult = null;
    const half = Math.max(90, (ev.d_m[1] / Math.max(ev.v_mps[0], 10)) * 4);
    view.tLo = Math.max(0, params.t0 - half);
    view.tHi = Math.min(duration, params.t0 + half);
    view.fLo = Math.max(0, params.f0 * 0.6);
    view.fHi = Math.min(2000, params.f0 * (harmonics >= 2 ? 2.4 : 1.5));
    renderEvents();
    renderFit();
    void load();
  }

  // ------------------------------------------------------------------------- silence list
  function addSilence(start: number, end: number): void {
    if (end - start < 1) return;
    state.events.silence.push({ start_s: Math.round(start * 10) / 10, end_s: Math.round(end * 10) / 10 });
    state.events.silence.sort((a, b) => a.start_s - b.start_s);
    save();
    emit("events");
    renderSilence();
    void redraw();
  }

  function renderSilence(): void {
    clear(silenceBox);
    const startIn = numberInput(0, () => undefined, { min: 0, max: duration, width: "90px" });
    const endIn = numberInput(0, () => undefined, { min: 0, max: duration, width: "90px" });
    silenceBox.append(
      h(
        "p",
        { class: "muted" },
        "Mark stretches where no aircraft is audible at all. Candidates with a known flight within 4 km during silence are penalized (soft) or dropped (hard); see the run options.",
      ),
      ...state.events.silence.map((w, i) =>
        h(
          "div",
          { class: "row" },
          pill(`${fmtClock(w.start_s)} – ${fmtClock(w.end_s)}`, "accent"),
          h("span", { class: "muted" }, `${(w.end_s - w.start_s).toFixed(0)} s`),
          button("Delete", () => {
            state.events.silence.splice(i, 1);
            save();
            emit("events");
            renderSilence();
            void redraw();
          }, "btn small danger"),
        ),
      ),
      h("div", { class: "row" }, field("start (s)", startIn), field("end (s)", endIn), button("Add", () => addSilence(Number(startIn.value), Number(endIn.value)), "btn small")),
    );
  }

  // ------------------------------------------------------------------------- import/export
  const fileIn = h("input", { type: "file", accept: ".json,application/json", style: { display: "none" } });
  fileIn.addEventListener("change", () => {
    const f = fileIn.files?.[0];
    if (!f) return;
    void f.text().then((text) => {
      try {
        const parsed = JSON.parse(text) as EventsFile;
        if (parsed.schema !== "earwitness.events/1" || !Array.isArray(parsed.events)) throw new Error("not an earwitness events file");
        state.events = { ...parsed, recording: { file: rec.name, sha256: rec.sha256 } };
        selected = null;
        save();
        emit("events");
        renderEvents();
        renderSilence();
        void redraw();
      } catch (err) {
        status.replaceChildren(notice("error", (err as Error).message));
      }
    });
  });

  container.append(
    h(
      "div",
      { class: "grid wide" },
      h(
        "div",
        { class: "card" },
        h("h2", {}, `Spectrogram: ${rec.name}`),
        h(
          "div",
          { class: "row" },
          field("f low (Hz)", fLoIn),
          field("f high (Hz)", fHiIn),
          field("from (s)", tLoIn),
          field("to (s)", tHiIn),
          button("Full view", () => {
            view.tLo = 0;
            view.tHi = duration;
            view.fLo = 0;
            view.fHi = 400;
            void load();
          }, "btn small"),
          status,
        ),
        modeButtons,
        plot,
        h(
          "p",
          { class: "muted" },
          "Whitened power (dB above each frequency's median over time), so steady tones fade and sweeping lines stand out. A propeller flyover is a line that falls through f0 at closest approach, with harmonics at 2 f0, 3 f0, …",
        ),
      ),
      h(
        "div",
        {},
        h("div", { class: "card" }, h("h2", {}, "Doppler fit"), fitBox),
        h("div", { class: "card", style: { marginTop: "16px" } }, h("h2", {}, "Events"), eventsBox),
        h("div", { class: "card", style: { marginTop: "16px" } }, h("h2", {}, "Silence windows"), silenceBox),
        h(
          "div",
          { class: "card", style: { marginTop: "16px" } },
          h(
            "div",
            { class: "row" },
            button("Export events.json", () => download("events.json", JSON.stringify(state.events, null, 2)), "btn small"),
            button("Import events.json", () => fileIn.click(), "btn small"),
            fileIn,
            button("Continue to run", () => go("run"), "btn primary"),
          ),
        ),
      ),
    ),
  );

  renderFit();
  renderEvents();
  renderSilence();
  void load();
  return () => plots.purge(plot);
}
