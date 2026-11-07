// Locate view: one step from a recording to an answer. The start time comes from the file when it
// carries one, the place is a point and a radius, and every method the recording has clues for
// runs with its flyovers measured automatically. The by-hand pages stay for checking and tuning.

import type { Feature, FeatureCollection, Polygon } from "geojson";
import type maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import { ApiError, api } from "../api";
import { button, clear, field, fmtClock, fmtDuration, h, notice, numberInput, pill, select, textInput } from "../dom";
import { createMap, fitBounds, marker } from "../map";
import { emit, save, state } from "../state";
import type { Bounds, FlyoversResult, JobSnapshot, LocateMethod, LocateSummary, StartInfo } from "../types";
import { recordingPicker } from "./audit";

function go(view: string): void {
  location.hash = `#/${view}`;
}

const RADII = [2, 5, 10, 25, 50, 100, 250];

function offsetText(minutes: number): string {
  const sign = minutes >= 0 ? "+" : "−";
  const a = Math.abs(minutes);
  return `UTC${sign}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
}

function zoneOptions(computerNow: number): [string, string][] {
  const out: [string, string][] = [
    ["computer", `this computer's time zone (now ${offsetText(computerNow)})`],
    ["utc", "UTC"],
  ];
  for (let m = -12 * 60; m <= 14 * 60; m += 30) if (m !== 0) out.push([String(m), offsetText(m)]);
  return out;
}

/** A wall-clock date and time in a zone ("computer", "utc" or minutes east of UTC) as a Date. */
export function toUtc(date: string, time: string, zone: string): Date | null {
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  const t = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(time || "00:00:00");
  if (!d || !t) return null;
  const [y, mo, day] = [Number(d[1]), Number(d[2]) - 1, Number(d[3])];
  const [hh, mm, ss] = [Number(t[1]), Number(t[2]), Number(t[3] ?? 0)];
  if (zone === "computer") return new Date(y, mo, day, hh, mm, ss);
  const offset = zone === "utc" ? 0 : Number(zone);
  return new Date(Date.UTC(y, mo, day, hh, mm, ss) - offset * 60000);
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function localParts(d: Date): { date: string; time: string } {
  return {
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
  };
}

function circle(lat: number, lon: number, radiusKm: number): Feature<Polygon> {
  const ring: [number, number][] = [];
  const dLat = radiusKm / 110.574;
  const dLon = radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= 72; i++) {
    const a = (i / 72) * 2 * Math.PI;
    ring.push([lon + dLon * Math.sin(a), lat + dLat * Math.cos(a)]);
  }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } };
}

function circleBounds(lat: number, lon: number, radiusKm: number): Bounds {
  const dLat = radiusKm / 110.574;
  const dLon = radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180));
  return [lon - dLon, lat - dLat, lon + dLon, lat + dLat];
}

function kmBetween(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = Math.PI / 180;
  const a =
    Math.sin(((lat2 - lat1) * r) / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 12742 * Math.asin(Math.min(1, Math.sqrt(a)));
}

function distanceText(km: number): string {
  return km < 1 ? `${Math.round(km * 1000)} m` : km < 20 ? `${km.toFixed(1)} km` : `${Math.round(km)} km`;
}

/** The runners-up of a method's answer, as distances and score gaps from the best. */
function rivals(m: LocateMethod): string {
  const [best, ...rest] = m.top ?? [];
  if (!best || !rest.length) return "";
  const parts = rest
    .slice(0, 3)
    .map((t) => `${distanceText(kmBetween(best.lat, best.lon, t.lat, t.lon))} away (score ${(best.score - t.score).toFixed(2)} lower)`);
  return `Next best: ${parts.join("; ")}.`;
}

const LEVEL: Record<string, [string, string]> = {
  clear: ["clear", "ok"],
  probable: ["probable", "warn"],
  possible: ["possible", "warn"],
  unsettled: ["not settled", "error"],
};

function levelPill(level: string | null): HTMLElement {
  const [text, kind] = LEVEL[level ?? ""] ?? ["no answer", ""];
  return pill(text, kind);
}

export function mountLocate(container: HTMLElement): () => void {
  const prefs = state.locate;
  let computerNow = -new Date().getTimezoneOffset();
  let timer: number | undefined;
  let map: maplibregl.Map | null = null;
  let pin: maplibregl.Marker | null = null;
  let resultMap: maplibregl.Map | null = null;

  // ------------------------------------------------------------------ 1. recording
  const heardBox = h("div");
  const hintBox = h("div");

  async function onRecording(): Promise<void> {
    clear(heardBox);
    clear(hintBox);
    const rec = state.recording;
    if (!rec) return;
    void loadStart(rec.id);
    void loadFlyovers(rec.id);
  }

  // ------------------------------------------------------------------ 2. when
  const dateInput = textInput(prefs.date, (v) => {
    prefs.date = v;
    save();
    renderWhen();
  }, { type: "date" });
  const timeInput = textInput(prefs.time, (v) => {
    prefs.time = v;
    save();
    renderWhen();
  }, { type: "time" });
  timeInput.step = "1";
  let zoneSelect = select(zoneOptions(computerNow), prefs.zone, (v) => setZone(v));
  const zoneSlot = h("span");
  zoneSlot.append(zoneSelect);
  const roughInput = numberInput(prefs.roughMinutes, (v) => {
    prefs.roughMinutes = Math.max(1, v);
    save();
    renderWhen();
  }, { min: 1, step: 1, width: "70px" });
  const whenText = h("p", { class: "muted" });
  const modeBox = h("div", { class: "stack" });

  function setZone(v: string): void {
    prefs.zone = v;
    save();
    renderWhen();
  }

  function modeOption(value: "exact" | "rough" | "day", label: string, extra?: HTMLElement): HTMLElement {
    const input = h("input", { type: "radio", name: "when-mode", checked: prefs.when === value });
    input.addEventListener("change", () => {
      if (!input.checked) return;
      prefs.when = value;
      save();
      renderWhen();
    });
    return h("label", { class: "check" }, input, h("span", {}, label), extra ?? null);
  }

  function renderWhen(): void {
    modeBox.replaceChildren(
      modeOption("exact", "to the second: the phone's own time, or a clock in the picture"),
      modeOption("rough", "roughly, within", h("span", { class: "row inline" }, roughInput, h("span", {}, "minutes"))),
      modeOption("day", "only the day"),
    );
    timeInput.disabled = prefs.when === "day";
    const start = toUtc(prefs.date, prefs.when === "day" ? "00:00:00" : prefs.time, prefs.zone);
    if (!prefs.date) whenText.textContent = "Enter the date the recording was made.";
    else if (!start) whenText.textContent = "That date or time is not complete.";
    else if (prefs.when === "day") {
      const end = new Date(start.getTime() + 86400000);
      whenText.textContent = `Searches ${start.toISOString().slice(0, 16).replace("T", " ")} to ${end.toISOString().slice(0, 16).replace("T", " ")} UTC. A thunder search then downloads the whole day's lightning records, about 2 GB.`;
    } else {
      const tol = prefs.when === "exact" ? "2 seconds" : `${prefs.roughMinutes} minutes`;
      whenText.textContent = `Started ${start.toISOString().slice(0, 19).replace("T", " ")} UTC, give or take ${tol}.`;
    }
  }

  async function loadStart(rid: string): Promise<void> {
    let info: StartInfo;
    try {
      info = await api.startInfo(rid);
    } catch (err) {
      hintBox.replaceChildren(notice("error", (err as Error).message));
      return;
    }
    computerNow = info.computer_offset_min;
    const fresh = select(zoneOptions(computerNow), prefs.zone, (v) => setZone(v));
    zoneSlot.replaceChildren(fresh);
    zoneSelect = fresh;
    const hint = info.start;
    if (!hint) {
      hintBox.replaceChildren(notice("info", "The file carries no start time. Enter it below as exactly as you know it."));
      return;
    }
    const use = () => {
      const start = new Date(hint.start_utc);
      if (hint.local && hint.utc_offset_min !== null) {
        // the file's own zone; call it "this computer's" when the two agree at that moment
        prefs.date = hint.local.slice(0, 10);
        prefs.time = hint.local.slice(11, 19);
        prefs.zone = -start.getTimezoneOffset() === hint.utc_offset_min ? "computer" : String(hint.utc_offset_min);
      } else {
        const parts = localParts(start);
        prefs.date = parts.date;
        prefs.time = parts.time;
        prefs.zone = "computer";
      }
      if (hint.exact) prefs.when = "exact";
      else {
        prefs.when = "rough";
        prefs.roughMinutes = Math.max(1, Math.ceil(hint.tolerance_s / 60));
      }
      prefs.forRecording = rid;
      save();
      dateInput.value = prefs.date;
      timeInput.value = prefs.time;
      roughInput.value = String(prefs.roughMinutes);
      zoneSelect.value = prefs.zone;
      renderWhen();
    };
    hintBox.replaceChildren(
      notice(hint.assumed_zone ? "warn" : "ok", `From the file: ${hint.note}`),
      h(
        "div",
        { class: "row" },
        button("Use the file's time", use),
        h("span", { class: "muted" }, "It is filled in when you open a file; change it if you know better."),
      ),
    );
    if (prefs.forRecording !== rid) use();
  }

  // ------------------------------------------------------------------ 3. where
  const radiusSelect = select(
    RADII.map((r) => [String(r), `within ${r} km`] as [string, string]),
    String(prefs.radiusKm),
    (v) => {
      prefs.radiusKm = Number(v);
      save();
      drawArea(true);
    },
  );
  const whereText = h("span", { class: "muted" });
  const mapEl = h("div", { class: "map small" });
  const centreInput = textInput(
    prefs.center ? `${prefs.center[0].toFixed(5)}, ${prefs.center[1].toFixed(5)}` : "",
    (v) => {
      const m = /^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/.exec(v);
      const lat = m ? Number(m[1]) : NaN;
      const lon = m ? Number(m[2]) : NaN;
      if (!(Math.abs(lat) <= 90 && Math.abs(lon) <= 180)) {
        whereText.textContent = "Type the centre as latitude, longitude in degrees, for example 35.2, -97.4.";
        return;
      }
      prefs.center = [lat, lon];
      save();
      drawArea(true);
    },
    { placeholder: "latitude, longitude", width: "14em" },
  );

  function drawArea(fit = false): void {
    const c = prefs.center;
    if (c && document.activeElement !== centreInput) centreInput.value = `${c[0].toFixed(5)}, ${c[1].toFixed(5)}`;
    whereText.textContent = c
      ? `Centre ${c[0].toFixed(4)}, ${c[1].toFixed(4)}; the search covers the square around this circle.`
      : "Click the map at roughly where the recording was made.";
    if (!map) return;
    const source = map.getSource("area") as GeoJSONSource | undefined;
    const data: FeatureCollection = { type: "FeatureCollection", features: c ? [circle(c[0], c[1], prefs.radiusKm)] : [] };
    source?.setData(data);
    pin?.remove();
    pin = c ? marker(map, c[1], c[0], "#1d4ed8") : null;
    if (fit && c) fitBounds(map, circleBounds(c[0], c[1], prefs.radiusKm));
  }

  function initMap(): void {
    if (map || !mapEl.isConnected) return;
    const c = prefs.center;
    map = createMap(mapEl, c ? [c[1], c[0]] : [-95, 38], c ? 8 : 3);
    const m = map;
    const addLayers = () => {
      if (m.getSource("area")) return;
      m.addSource("area", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "area-fill", type: "fill", source: "area", paint: { "fill-color": "#1d4ed8", "fill-opacity": 0.1 } });
      m.addLayer({ id: "area-line", type: "line", source: "area", paint: { "line-color": "#1d4ed8", "line-width": 2 } });
      drawArea(Boolean(c));
    };
    m.on("load", addLayers);
    m.on("click", (e) => {
      prefs.center = [e.lngLat.lat, e.lngLat.lng];
      save();
      drawArea();
    });
  }

  // ------------------------------------------------------------------ what it heard
  async function loadFlyovers(rid: string): Promise<void> {
    heardBox.replaceChildren(notice("info", "Listening for propeller flyovers…"));
    let res: FlyoversResult;
    try {
      res = await api.flyovers(rid);
    } catch (err) {
      heardBox.replaceChildren(notice("error", (err as Error).message));
      return;
    }
    const list = res.flyovers.slice(0, 6);
    if (!list.length) {
      heardBox.replaceChildren(
        h("p", { class: "muted" }, "No propeller flyover found. The search can still use thunder, if the recording has any."),
      );
      return;
    }
    heardBox.replaceChildren(
      h("p", {}, `${res.flyovers.length} propeller flyover${res.flyovers.length === 1 ? "" : "s"} measured; the strongest two are used:`),
      h(
        "ul",
        { class: "plain" },
        ...list.map((f, i) =>
          h(
            "li",
            { class: i < 2 ? "" : "muted" },
            `closest at ${fmtClock(f.heard_s)} into the clip: a line at ${f.f0_hz.toFixed(0)} Hz, `,
            `about ${f.v_mps.toFixed(0)} m/s, about ${(f.d_m / 1000).toFixed(1)} km away`,
            f.lines_hz.length > 1 ? `, ${f.lines_hz.length} lines` : "",
          ),
        ),
      ),
      h(
        "p",
        { class: "muted" },
        res.flyovers.length === 1 ? "With one flyover the start time must be known to the second." : "",
      ),
    );
  }

  // ------------------------------------------------------------------ locate
  const locateBtn = button("Locate", () => void start(), "btn primary");
  const statusLine = h("div", { class: "row" });
  const log = h("pre", { class: "log" });
  const lastLine = h("span", { class: "muted" });
  const logBox = h("details", {}, h("summary", { class: "muted" }, "the full log"), log);
  const resultBox = h("div");
  const warn = h("div");

  function request(): Record<string, unknown> | string {
    const rec = state.recording;
    if (!rec) return "Open a recording first.";
    if (!prefs.center) return "Click the map at roughly where the recording was made.";
    if (!prefs.date) return "Enter the date the recording was made.";
    const body: Record<string, unknown> = {
      recording_id: rec.id,
      center: prefs.center,
      radius_km: prefs.radiusKm,
    };
    if (prefs.when === "day") {
      const first = toUtc(prefs.date, "00:00:00", prefs.zone);
      if (!first) return "That date is not complete.";
      const last = new Date(first.getTime() + 86400000 - 1000);
      body.date = first.toISOString().slice(0, 10);
      const lastDay = last.toISOString().slice(0, 10);
      if (lastDay !== body.date) body.date_end = lastDay;
    } else {
      const startAt = toUtc(prefs.date, prefs.time, prefs.zone);
      if (!startAt) return "Enter the start time, or choose 'only the day'.";
      body.start_utc = startAt.toISOString();
      body.tolerance_s = prefs.when === "exact" ? 2 : prefs.roughMinutes * 60;
    }
    return body;
  }

  async function start(): Promise<void> {
    const body = request();
    if (typeof body === "string") {
      warn.replaceChildren(notice("warn", body));
      return;
    }
    warn.replaceChildren();
    locateBtn.disabled = true;
    log.textContent = "";
    clear(resultBox);
    try {
      const job = await api.startLocate(body);
      prefs.jobId = job.id;
      save();
      emit("job");
      watch(job.id);
    } catch (err) {
      statusLine.replaceChildren(notice("error", (err as Error).message));
      locateBtn.disabled = false;
    }
  }

  function watch(jobId: string): void {
    window.clearInterval(timer);
    let since = 0;
    const tick = async () => {
      let snap: JobSnapshot;
      try {
        snap = await api.job(jobId, since);
      } catch (err) {
        window.clearInterval(timer);
        locateBtn.disabled = false;
        if (err instanceof ApiError && err.status === 404) {
          prefs.jobId = null;
          save();
          return;
        }
        statusLine.replaceChildren(notice("error", (err as Error).message));
        return;
      }
      if (snap.lines.length) {
        log.textContent += snap.lines.join("\n") + "\n";
        log.scrollTop = log.scrollHeight;
        const last = snap.lines[snap.lines.length - 1];
        lastLine.textContent = last.length > 140 ? `${last.slice(0, 140)}…` : last;
      }
      since = snap.next;
      const kind = snap.status === "done" ? "ok" : snap.status === "failed" ? "error" : "accent";
      statusLine.replaceChildren(
        pill(snap.status === "running" ? "searching" : snap.status, kind),
        h("span", { class: "muted" }, snap.elapsed_s !== null ? fmtDuration(snap.elapsed_s) : "waiting for the previous run"),
        lastLine,
      );
      if (snap.status === "done" || snap.status === "failed") {
        window.clearInterval(timer);
        locateBtn.disabled = false;
        emit("job");
        if (snap.status === "failed") statusLine.append(notice("error", snap.error ?? "failed"));
        else renderResult(snap.result as unknown as LocateSummary);
      }
    };
    void tick();
    timer = window.setInterval(() => void tick(), 1000);
  }

  function renderResult(s: LocateSummary): void {
    resultMap?.remove();
    resultMap = null;
    const a = s.answer;
    const rows = s.methods.map((m) =>
      h(
        "tr",
        {},
        h("th", {}, m.method),
        h("td", {}, m.status === "ran" && m.lat !== null ? levelPill(m.level) : pill(m.status, m.status === "failed" ? "error" : "")),
        h(
          "td",
          {},
          m.status === "ran" && m.lat !== null
            ? `${m.lat.toFixed(5)}, ${m.lon!.toFixed(5)}. ${m.sentence ?? ""}`
            : m.reason || (m.sentence ?? ""),
        ),
        h("td", { class: "muted" }, m.runtime_s !== null ? fmtDuration(m.runtime_s) : ""),
      ),
    );
    const card = h("div", { class: "card", style: { marginTop: "16px" } }, h("h2", {}, "Answer"));
    if (a) {
      const mapEl2 = h("div", { class: "map small" });
      card.append(
        h("p", { class: "answer" }, h("strong", {}, `${a.lat.toFixed(5)}, ${a.lon.toFixed(5)}`), " ", levelPill(a.level), " ", h("span", { class: "muted" }, `by ${a.method === "aircraft" ? "aircraft Doppler" : "thunder"}`)),
        h("p", {}, a.sentence),
        h("p", { class: "muted" }, rivals(s.methods.find((m) => m.method === a.method) ?? s.methods[0])),
        mapEl2,
        h(
          "div",
          { class: "row" },
          a.run_id
            ? button("See it on the Results page", () => {
                state.runId = a.run_id ?? null;
                save();
                go("results");
              })
            : null,
          a.method === "aircraft" && a.run_id ? button("Check the flyovers it measured", () => void review(a.run_id!, s)) : null,
        ),
      );
      requestAnimationFrame(() => {
        if (!mapEl2.isConnected) return;
        resultMap = createMap(mapEl2, [a.lon, a.lat], 12);
        marker(resultMap, a.lon, a.lat, "#dc2626");
        const c = prefs.center;
        resultMap.on("load", () => {
          if (!resultMap) return;
          if (c) {
            resultMap.addSource("area", { type: "geojson", data: { type: "FeatureCollection", features: [circle(c[0], c[1], prefs.radiusKm)] } });
            resultMap.addLayer({ id: "area-line", type: "line", source: "area", paint: { "line-color": "#1d4ed8", "line-width": 2 } });
          }
        });
      });
    } else {
      card.append(notice("warn", "No answer. The table says what each method found or why it did not run."));
    }
    card.append(
      h("table", { class: "table" }, h("tbody", {}, ...rows)),
      h("p", { class: "muted" }, `Finished in ${fmtDuration(s.runtime_s)}. Each method's run folder is on the Results page, with its heat map and ranked answers.`),
    );
    resultBox.replaceChildren(card);
  }

  async function review(runId: string, s: LocateSummary): Promise<void> {
    try {
      state.events = await api.runEvents(runId);
    } catch (err) {
      resultBox.append(notice("error", (err as Error).message));
      return;
    }
    state.method = "aircraft";
    state.priors.date = s.time_prior.window_start_utc.slice(0, 10);
    state.priors.dateEnd = "";
    state.priors.bbox = s.region;
    state.priors.knownTime = s.time_prior.start_utc ?? "";
    state.priors.knownTimeTolerance = s.time_prior.tolerance_s ?? 2;
    save();
    emit("method");
    go("audio");
  }

  // ------------------------------------------------------------------ layout
  container.append(
    h(
      "div",
      { class: "card" },
      h("h2", {}, "Find where a recording was made"),
      h(
        "p",
        {},
        "Open a recording of your own, say roughly when and where it was made, and press Locate. ",
        "Earwitness measures the propeller flyovers and thunder in it, downloads the public flight and lightning records for that time and area, and says how sure the answer is. ",
        "Everything runs on this computer.",
      ),
      recordingPicker(() => void onRecording()),
      heardBox,
    ),
    h(
      "div",
      { class: "grid", style: { marginTop: "16px" } },
      h(
        "div",
        { class: "card" },
        h("h2", {}, "When did it start?"),
        hintBox,
        h("div", { class: "row" }, field("Date", dateInput), field("Time", timeInput), field("Time zone", zoneSlot)),
        h("h3", {}, "How sure is that time?"),
        modeBox,
        whenText,
        h("p", { class: "muted" }, "To the second lets a single flyover place the recording. Without the time, two flyovers are needed and the search takes longer."),
      ),
      h(
        "div",
        { class: "card" },
        h("h2", {}, "Roughly where?"),
        h("div", { class: "row" }, field("Centre", centreInput, "click the map, or type it"), field("Radius", radiusSelect)),
        h("p", {}, whereText),
        mapEl,
        h("p", { class: "muted" }, "A smaller area is faster: an aircraft search over 25 km takes a minute or two once the day's flights are downloaded. The first search for a day downloads that day's flight records, several GB."),
      ),
    ),
    h(
      "div",
      { class: "card", style: { marginTop: "16px" } },
      h("div", { class: "row" }, locateBtn, h("span", { class: "muted" }, "One search at a time; you can leave this page and come back.")),
      warn,
      statusLine,
      logBox,
    ),
    resultBox,
  );

  renderWhen();
  drawArea();
  requestAnimationFrame(initMap);
  if (state.recording) void onRecording();
  if (prefs.jobId) watch(prefs.jobId);

  return () => {
    window.clearInterval(timer);
    map?.remove();
    map = null;
    resultMap?.remove();
    resultMap = null;
  };
}
