import "./styles.css";
import { api, initToken } from "./api";
import { h, clear } from "./dom";
import { on, save, state, type ViewName } from "./state";
import { mountAudit } from "./views/audit";
import { mountSetup } from "./views/setup";
import { mountAudio } from "./views/audio";
import { mountRun } from "./views/run";
import { mountResults } from "./views/results";

type Mount = (container: HTMLElement) => () => void;

const VIEWS: { name: ViewName; label: string; mount: Mount; needs?: () => string | null }[] = [
  { name: "audit", label: "Audit", mount: mountAudit },
  { name: "setup", label: "Setup", mount: mountSetup },
  {
    name: "audio",
    label: "Audio",
    mount: mountAudio,
    needs: () => (state.recording ? (state.method === "audit" ? "choose a method in Setup" : null) : "open a recording in Setup"),
  },
  {
    name: "run",
    label: "Run",
    mount: mountRun,
    needs: () => (state.recording ? (state.method === "audit" ? "choose a method in Setup" : null) : "open a recording in Setup"),
  },
  { name: "results", label: "Results", mount: mountResults },
];

const root = document.getElementById("app")!;
const nav = h("nav", { class: "nav" });
const statusBox = h("div", { class: "status" });
const main = h("main");
let cleanup: (() => void) | null = null;

function viewFromHash(): ViewName | null {
  const name = location.hash.replace(/^#\/?/, "") as ViewName;
  return VIEWS.some((v) => v.name === name) ? name : null;
}

export function show(name: ViewName): void {
  const view = VIEWS.find((v) => v.name === name) ?? VIEWS[0];
  const blocker = view.needs?.();
  if (blocker) {
    main.replaceChildren(h("div", { class: "notice warn" }, `To use ${view.label}, ${blocker}.`));
    state.view = name;
  } else {
    cleanup?.();
    clear(main);
    cleanup = view.mount(main);
    state.view = name;
  }
  save();
  if (location.hash !== `#/${name}`) history.replaceState(null, "", `#/${name}`);
  renderNav();
}

function renderNav(): void {
  clear(nav);
  for (const view of VIEWS) {
    const blocker = view.needs?.();
    nav.append(
      h(
        "button",
        {
          class: view.name === state.view ? "active" : "",
          title: blocker ?? "",
          onclick: () => show(view.name),
        },
        view.label,
      ),
    );
  }
}

async function refreshStatus(): Promise<void> {
  try {
    const health = await api.health();
    statusBox.replaceChildren(
      h("div", {}, `earwitness ${health.version}`, health.busy ? " · a run is in progress" : ""),
      h("div", {}, `runs: ${health.runs_dir}`),
      h("div", {}, health.ffmpeg ? "ffmpeg found: all audio and video containers" : "ffmpeg not found: WAV, FLAC and OGG only"),
    );
  } catch (err) {
    statusBox.replaceChildren(h("div", { class: "pill error" }, `server unreachable: ${(err as Error).message}`));
  }
}

async function reopenRecording(): Promise<void> {
  if (!state.recording) return;
  try {
    const info = await api.openRecording(state.recording.path);
    state.recording = info;
  } catch {
    state.recording = null;
  }
  save();
}

function footer(): HTMLElement {
  const link = (href: string, text: string) => h("a", { href, target: "_blank", rel: "noreferrer" }, text);
  return h(
    "footer",
    {},
    "Data: lightning NOAA GOES GLM (open data) · flights adsb.lol globe_history (ODbL 1.0) · ",
    "aircraft types ICAO Doc 8643 via FlightAware dump1090 · terrain Copernicus GLO-30 © DLR e.V. 2010-2014 ",
    "and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; ",
    "all rights reserved · geoid EGM96 (NGA) · land mask Natural Earth · basemap © ",
    link("https://www.openstreetmap.org/copyright", "OpenStreetMap"),
    " contributors (configurable in Setup). Everything runs on this computer; nothing is uploaded. ",
    link("https://earwitness.dev/data.html", "Data sources"),
    " · ",
    link("./third-party-licenses.txt", "Software licences"),
    " · ",
    link("https://earwitness.dev", "earwitness.dev"),
  );
}

async function boot(): Promise<void> {
  initToken();
  root.append(
    h(
      "header",
      { class: "topbar" },
      h("div", { class: "brand" }, "Earwitness", h("small", {}, "does my recording contain location clues?")),
      nav,
      statusBox,
    ),
    main,
    footer(),
  );
  await refreshStatus();
  await reopenRecording();
  on("recording", renderNav);
  on("method", renderNav);
  on("job", () => void refreshStatus());
  window.addEventListener("hashchange", () => {
    const name = viewFromHash();
    if (name && name !== state.view) show(name);
  });
  show(viewFromHash() ?? state.view);
  setInterval(() => void refreshStatus(), 15000);
}

void boot();
