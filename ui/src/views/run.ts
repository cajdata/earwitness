// Run view: what will be searched, what data is cached, the method's options, a compute
// estimate, and the run itself with its live log.

import { ApiError, api } from "../api";
import { button, checkbox, clear, field, fmtDuration, h, notice, numberInput, pill, select } from "../dom";
import { emit, save, state } from "../state";
import type { Bounds, DataStatus, JobSnapshot, RunResult } from "../types";

function go(view: string): void {
  location.hash = `#/${view}`;
}

export function mountRun(container: HTMLElement): () => void {
  const rec = state.recording!;
  const priors = state.priors;
  const method = state.method as "thunder" | "aircraft";
  const bbox = priors.bbox;
  let timer: number | undefined;

  const problems: string[] = [];
  if (!priors.date) problems.push("the recording date is not set (Setup)");
  if (!bbox) problems.push("no search region (Setup)");
  if (method === "aircraft") {
    const n = state.events.events.length;
    if (n === 0) problems.push("no flyover events (Audio)");
    else if (n === 1 && !priors.knownTime) problems.push("one event needs the known recording start time (Setup) or a second event");
  }

  // ------------------------------------------------------------------------- summary
  const summary = h(
    "div",
    { class: "card" },
    h("h2", {}, "What will run"),
    h(
      "table",
      { class: "table" },
      h("tbody", {},
        h("tr", {}, h("th", {}, "recording"), h("td", {}, `${rec.name} (${rec.duration_s.toFixed(0)} s)`)),
        h("tr", {}, h("th", {}, "method"), h("td", {}, method === "thunder" ? "thunder against GOES GLM flashes" : "aircraft Doppler against adsb.lol tracks")),
        h("tr", {}, h("th", {}, "dates (UTC)"), h("td", {}, priors.date ? `${priors.date}${priors.dateEnd ? ` to ${priors.dateEnd}` : ""}` : "not set")),
        h("tr", {}, h("th", {}, "region"), h("td", {}, bbox ? bbox.map((x) => x.toFixed(3)).join(", ") : "not set")),
        method === "aircraft"
          ? h("tr", {}, h("th", {}, "events"), h("td", {}, `${state.events.events.length} flyover(s), ${state.events.silence.length} silence window(s)` + (priors.knownTime ? `, start known: ${priors.knownTime} ± ${priors.knownTimeTolerance} s` : "")))
          : null,
      ),
    ),
    ...problems.map((p) => notice("warn", p)),
  );

  // ------------------------------------------------------------------------- data status
  const dataBox = h("div");
  async function loadStatus(): Promise<void> {
    if (!bbox || !priors.date) {
      dataBox.replaceChildren(h("p", { class: "muted" }, "Set the date and region to see what is cached."));
      return;
    }
    dataBox.replaceChildren(h("p", { class: "muted" }, "checking the cache…"));
    try {
      const s = await api.dataStatus(priors.date, bbox, state.aircraft.variant);
      renderStatus(s);
    } catch (err) {
      dataBox.replaceChildren(notice("error", (err as Error).message));
    }
  }
  function renderStatus(s: DataStatus): void {
    clear(dataBox);
    dataBox.append(h("p", { class: "mono muted" }, `cache: ${s.cache_root}`));
    if (method === "thunder") {
      dataBox.append(
        h("p", {}, `GLM ${s.glm.satellite}: ${s.glm.hours_cached} of 24 hours of ${priors.date} cached. `, h("span", { class: "muted" }, "Missing hours download during the run (a few MB each, anonymous S3).")),
      );
    } else {
      dataBox.append(
        h(
          "div",
          {},
        h(
          "p",
          {},
          s.adsb.covered ? pill("ADS-B day cached for this region", "ok") : pill("ADS-B day not cached for this region", "warn"),
          " ",
          s.adsb.types_cached ? pill("aircraft types cached", "ok") : pill("aircraft types download on first use", ""),
        ),
        s.adsb.regions.length
          ? h("ul", { class: "muted" }, ...s.adsb.regions.map((r) => h("li", { class: "mono" }, `${r.bbox.map((x) => x.toFixed(2)).join(", ")}${r.covers ? " (covers the region)" : ""}`)))
          : null,
        s.adsb.covered
          ? null
          : h(
              "div",
              { class: "row" },
              button("Fetch the adsb.lol day for this region", () => void fetchAdsb(), "btn"),
              h("span", { class: "muted" }, "Downloads the day's release (gigabytes) and keeps only this region as Parquet; this can take a long time."),
            ),
        ),
      );
    }
  }
  async function fetchAdsb(): Promise<void> {
    if (!bbox) return;
    try {
      const job = await api.fetchAdsb(priors.date, bbox as Bounds, state.aircraft.variant);
      watch(job.id, () => void loadStatus());
    } catch (err) {
      dataBox.replaceChildren(notice("error", (err as Error).message));
    }
  }

  // ------------------------------------------------------------------------- options
  const optionsBox = h("div");
  function renderOptions(): void {
    clear(optionsBox);
    if (method === "thunder") {
      const t = state.thunder;
      optionsBox.append(
        h(
          "div",
          { class: "row" },
          field("offset step (s)", numberInput(t.step_s, (v) => ((t.step_s = v), save(), estimate()), { min: 0.5, step: 0.5, width: "80px" })),
          field("audible radius (km)", numberInput(t.rmax_km, (v) => ((t.rmax_km = v), save()), { min: 1, step: 1, width: "80px" })),
          field("coarse grid (km)", numberInput(t.grid_km, (v) => ((t.grid_km = v), save(), estimate()), { min: 0.5, step: 0.5, width: "80px" })),
          field("regions to refine", numberInput(t.top, (v) => ((t.top = Math.round(v)), save()), { min: 1, max: 50, step: 1, width: "80px" })),
          field("bearing weight (0 to 1)", numberInput(t.bearing_weight, (v) => ((t.bearing_weight = v), save()), { min: 0, max: 1, step: 0.1, width: "80px" })),
          field("score", select([["cosine", "cosine"], ["pearson", "pearson (mean removed)"]], t.score, (v) => ((t.score = v), save()))),
          field("GLM satellite", select([["auto", "auto"], ["east", "GOES-East"], ["west", "GOES-West"]], t.satellite, (v) => ((t.satellite = v), save()))),
        ),
        h(
          "div",
          { class: "row" },
          checkbox("refine the best regions", t.refine, (v) => ((t.refine = v), save())),
          checkbox("land mask (drop candidates at sea)", t.land_mask, (v) => ((t.land_mask = v), save())),
          checkbox("mask wind-flagged frames", t.mask_wind, (v) => ((t.mask_wind = v), save())),
        ),
      );
    } else {
      const a = state.aircraft;
      optionsBox.append(
        h(
          "div",
          { class: "row" },
          field(
            "silence windows",
            select(
              [["soft", "soft: 3 dB penalty per flight heard in silence"], ["hard", "hard: drop the candidate"], ["off", "off"]],
              a.silence,
              (v) => ((a.silence = v), save()),
            ),
            "soft is right for real recordings, where ADS-B coverage has gaps",
          ),
          field("flight pairs to refine", numberInput(a.top, (v) => ((a.top = Math.round(v)), save()), { min: 1, max: 50, step: 1, width: "80px" })),
          field("max candidates scored", numberInput(a.max_candidates, (v) => ((a.max_candidates = Math.round(v)), save()), { min: 1000, step: 1000, width: "100px" })),
          field("harmonics scored (at least)", numberInput(a.harmonics, (v) => ((a.harmonics = Math.round(v)), save()), { min: 1, max: 12, step: 1, width: "70px" })),
          field("adsb.lol instance", select([["prod", "prod"], ["staging", "staging"]], a.variant, (v) => ((a.variant = v), save(), void loadStatus()))),
        ),
        h("div", { class: "row" }, checkbox("terrain heights (downloads DEM tiles on first use)", a.terrain, (v) => ((a.terrain = v), save()))),
      );
    }
  }

  // ------------------------------------------------------------------------- estimate
  const estimateBox = h("p", { class: "muted" }, "…");
  async function estimate(): Promise<void> {
    if (!bbox || !priors.date) {
      estimateBox.textContent = "Set the date and region for an estimate.";
      return;
    }
    try {
      const e = await api.estimate({
        method,
        date: priors.date,
        date_end: priors.dateEnd || null,
        bbox,
        known_time: method === "aircraft" && state.events.events.length < 2 && Boolean(priors.knownTime),
        step_s: state.thunder.step_s,
        grid_km: state.thunder.grid_km,
      });
      estimateBox.textContent = `Rough size: ${e.text}. (Calibrated on the validation runs; a guide to whether to narrow the priors.)`;
    } catch (err) {
      estimateBox.textContent = (err as Error).message;
    }
  }

  // ------------------------------------------------------------------------- run and log
  const log = h("pre", { class: "log" });
  const statusLine = h("div", { class: "row" });
  const runBtn = button("Run the search", () => void start(), "btn primary");
  runBtn.disabled = problems.length > 0;
  const resultsBtn = button("Open results", () => go("results"), "btn");
  resultsBtn.style.display = "none";

  function body(): Record<string, unknown> {
    const common = {
      recording_id: rec.id,
      date: priors.date,
      date_end: priors.dateEnd || null,
      bbox,
    };
    if (method === "thunder") return { ...common, ...state.thunder };
    const events = { ...state.events, recording: { file: rec.name, sha256: rec.sha256 }, known_time_utc: priors.knownTime || null, known_time_tolerance_s: priors.knownTimeTolerance };
    return {
      ...common,
      events,
      known_time_utc: priors.knownTime || null,
      known_time_tolerance_s: priors.knownTimeTolerance,
      ...state.aircraft,
    };
  }

  async function start(): Promise<void> {
    runBtn.disabled = true;
    log.textContent = "";
    resultsBtn.style.display = "none";
    try {
      const job = method === "thunder" ? await api.startThunder(body()) : await api.startAircraft(body());
      state.jobId = job.id;
      state.runId = null;
      save();
      emit("job");
      watch(job.id);
    } catch (err) {
      statusLine.replaceChildren(notice("error", (err as Error).message));
      runBtn.disabled = problems.length > 0;
    }
  }

  function watch(jobId: string, onDone?: () => void): void {
    window.clearInterval(timer);
    let since = 0;
    const tick = async () => {
      let snap: JobSnapshot;
      try {
        snap = await api.job(jobId, since);
      } catch (err) {
        window.clearInterval(timer);
        if (err instanceof ApiError && err.status === 404) {
          // a job from an earlier server session; nothing to show
          state.jobId = null;
          save();
          return;
        }
        statusLine.replaceChildren(notice("error", (err as Error).message));
        return;
      }
      if (snap.lines.length) {
        log.textContent += snap.lines.join("\n") + "\n";
        log.scrollTop = log.scrollHeight;
      }
      since = snap.next;
      const kind = snap.status === "done" ? "ok" : snap.status === "failed" ? "error" : "accent";
      statusLine.replaceChildren(
        pill(`${snap.label}: ${snap.status}`, kind),
        h("span", { class: "muted" }, snap.elapsed_s !== null ? `elapsed ${fmtDuration(snap.elapsed_s)}` : "queued"),
      );
      if (snap.status === "done" || snap.status === "failed") {
        window.clearInterval(timer);
        runBtn.disabled = problems.length > 0;
        emit("job");
        if (snap.status === "failed") statusLine.append(notice("error", snap.error ?? "failed"));
        const result = snap.result as RunResult | null;
        if (snap.status === "done" && result && typeof result.run_id === "string") {
          state.runId = result.run_id;
          save();
          resultsBtn.style.display = "";
          const best = result.top?.[0];
          if (best) statusLine.append(h("span", {}, `best: ${best.lat.toFixed(4)}, ${best.lon.toFixed(4)} (score ${best.score.toFixed(3)}, ${fmtDuration(result.runtime_s)})`));
        }
        onDone?.();
      }
    };
    void tick();
    timer = window.setInterval(() => void tick(), 1000);
  }

  container.append(
    h("div", { class: "grid" }, summary, h("div", { class: "card" }, h("h2", {}, "Data"), dataBox)),
    h("div", { class: "card", style: { marginTop: "16px" } }, h("h2", {}, "Options"), optionsBox, h("h3", {}, "Compute estimate"), estimateBox),
    h(
      "div",
      { class: "card", style: { marginTop: "16px" } },
      h("h2", {}, "Run"),
      h("div", { class: "row" }, runBtn, resultsBtn, h("span", { class: "muted" }, "One run at a time. The run folder holds everything needed to reproduce it; the log below is also written there.")),
      statusLine,
      log,
    ),
  );

  renderOptions();
  void loadStatus();
  void estimate();
  if (state.jobId) watch(state.jobId);

  return () => window.clearInterval(timer);
}
