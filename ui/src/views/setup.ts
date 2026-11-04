// Setup view: the recording, the method, and the priors (dates, region, known start time).

import { button, field, h, notice, numberInput, textInput } from "../dom";
import { BBoxDrawer, DEFAULT_TILES, baseStyle, createMap, fitBounds, setTileUrl, tileUrl } from "../map";
import { emit, save, state, type Method } from "../state";
import type { Bounds } from "../types";
import { recordingPicker } from "./audit";

function go(view: string): void {
  location.hash = `#/${view}`;
}

const METHODS: { id: Method; title: string; text: string }[] = [
  {
    id: "audit",
    title: "Audit only",
    text: "Report which clues the recording contains without searching for a location.",
  },
  {
    id: "thunder",
    title: "Thunder",
    text: "Match thunder claps against GOES GLM lightning flashes. Kilometre-level; needs the date and a region; works best with a long clip and many claps.",
  },
  {
    id: "aircraft",
    title: "Aircraft Doppler",
    text: "Match propeller flyovers against adsb.lol tracks. About 100 m; needs two flyovers, or one plus the recording's start time, and the date and a region.",
  },
];

function areaKm2(b: Bounds): number {
  const [w, s, e, n] = b;
  const midLat = ((s + n) / 2) * (Math.PI / 180);
  const dx = (e - w) * 111.32 * Math.cos(midLat);
  const dy = (n - s) * 110.57;
  return dx * dy;
}

export function mountSetup(container: HTMLElement): () => void {
  const priors = state.priors;

  // ---------------------------------------------------------------- method
  const methodBox = h("div", { class: "methods" });
  const renderMethods = () => {
    methodBox.replaceChildren(
      ...METHODS.map((m) =>
        h(
          "div",
          {
            class: `method ${state.method === m.id ? "active" : ""}`,
            onclick: () => {
              state.method = m.id;
              save();
              emit("method");
              renderMethods();
              renderPriors();
            },
          },
          h("strong", {}, m.title),
          h("span", { class: "muted" }, m.text),
        ),
      ),
    );
  };
  renderMethods();

  // ---------------------------------------------------------------- priors
  const bboxInputs = {
    w: numberInput(priors.bbox?.[0] ?? -100, (v) => setBBoxPart(0, v), { step: 0.01 }),
    s: numberInput(priors.bbox?.[1] ?? 30, (v) => setBBoxPart(1, v), { step: 0.01 }),
    e: numberInput(priors.bbox?.[2] ?? -85, (v) => setBBoxPart(2, v), { step: 0.01 }),
    n: numberInput(priors.bbox?.[3] ?? 42, (v) => setBBoxPart(3, v), { step: 0.01 }),
  };
  const areaBox = h("span", { class: "muted" });
  const mapEl = h("div", { class: "map small" });
  let map: ReturnType<typeof createMap> | null = null;
  let drawer: BBoxDrawer | null = null;
  const drawBtn = button("Draw region on the map", () => {
    if (!drawer) return;
    if (drawer.drawing) {
      drawer.disable();
      drawBtn.classList.remove("toggled");
    } else {
      drawer.enable();
      drawBtn.classList.add("toggled");
    }
  });

  function setBBox(b: Bounds | null): void {
    priors.bbox = b;
    save();
    if (b) {
      bboxInputs.w.value = b[0].toFixed(4);
      bboxInputs.s.value = b[1].toFixed(4);
      bboxInputs.e.value = b[2].toFixed(4);
      bboxInputs.n.value = b[3].toFixed(4);
      areaBox.textContent = `${areaKm2(b).toLocaleString(undefined, { maximumFractionDigits: 0 })} km²`;
    } else {
      areaBox.textContent = "no region yet";
    }
    drawer?.set(b);
    drawBtn.classList.remove("toggled");
  }

  function setBBoxPart(index: number, value: number): void {
    const b: Bounds = priors.bbox ? [...priors.bbox] : [Number(bboxInputs.w.value), Number(bboxInputs.s.value), Number(bboxInputs.e.value), Number(bboxInputs.n.value)];
    b[index] = value;
    if (b[0] < b[2] && b[1] < b[3]) {
      setBBox(b);
      if (map) fitBounds(map, b);
    }
  }

  const tilesInput = textInput(tileUrl(), (v) => {
    setTileUrl(v.trim() || DEFAULT_TILES);
    map?.setStyle(baseStyle());
  }, { width: "26em" });

  const priorsBox = h("div");
  function renderPriors(): void {
    priorsBox.replaceChildren();
    if (state.method === "audit") {
      priorsBox.append(h("p", { class: "muted" }, "Auditing needs no priors. Pick Thunder or Aircraft Doppler to locate the recording."));
      return;
    }
    const dateInput = textInput(priors.date, (v) => {
      priors.date = v;
      save();
    }, { type: "date" });
    const dateEndInput = textInput(priors.dateEnd, (v) => {
      priors.dateEnd = v;
      save();
    }, { type: "date" });
    const rows: HTMLElement[] = [
      h(
        "div",
        { class: "row" },
        field("Recording date (UTC)", dateInput, "the day the clip was recorded"),
        field("Last possible date", dateEndInput, "optional; every day in between is searched"),
      ),
    ];
    if (state.method === "aircraft") {
      const knownInput = textInput(priors.knownTime, (v) => {
        priors.knownTime = v.trim();
        save();
      }, { placeholder: "2026-09-08T18:04:30Z", width: "16em" });
      const tolInput = numberInput(priors.knownTimeTolerance, (v) => {
        priors.knownTimeTolerance = v;
        save();
      }, { min: 0.1, step: 0.5, width: "80px" });
      rows.push(
        h(
          "div",
          { class: "row" },
          field("Known recording start (UTC, optional)", knownInput, "with one flyover the start time must be known to about a second"),
          field("Tolerance (s)", tolInput),
        ),
      );
    }
    priorsBox.append(
      ...rows,
      h("h3", {}, "Search region"),
      h(
        "p",
        { class: "muted" },
        "The UI always searches a region (the CLI can search the whole world). Draw a rectangle with two clicks, type its bounds, or use the current map view.",
      ),
      h(
        "div",
        { class: "row" },
        field("West", bboxInputs.w),
        field("South", bboxInputs.s),
        field("East", bboxInputs.e),
        field("North", bboxInputs.n),
        areaBox,
      ),
      h(
        "div",
        { class: "row" },
        drawBtn,
        button("Use the map view", () => {
          if (!map) return;
          const b = map.getBounds();
          setBBox([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
        }),
        button("Clear", () => setBBox(null)),
      ),
      mapEl,
      h("div", { class: "row" }, field("Basemap tile URL", tilesInput, "default OpenStreetMap; set to blank for no basemap")),
      h("p", { class: "muted" }, "Thunder works at kilometre scale, so a region of a few hundred kilometres is fine; the aircraft funnel over a 15 by 12 degree region and a whole day takes 3 to 40 minutes."),
    );
    requestAnimationFrame(initMap);
  }

  function initMap(): void {
    if (map || !mapEl.isConnected) return;
    const center: [number, number] = priors.bbox ? [(priors.bbox[0] + priors.bbox[2]) / 2, (priors.bbox[1] + priors.bbox[3]) / 2] : [-95, 38];
    map = createMap(mapEl, center, priors.bbox ? 6 : 3);
    drawer = new BBoxDrawer(map, (b) => setBBox(b));
    map.once("load", () => {
      setBBox(priors.bbox);
      if (priors.bbox) fitBounds(map!, priors.bbox);
    });
  }

  renderPriors();

  // ---------------------------------------------------------------- layout
  const nextBtn = button("Continue", () => {
    if (state.method === "audit") go("audit");
    else if (!state.recording) warn.replaceChildren(notice("warn", "Open a recording first."));
    else if (!priors.date) warn.replaceChildren(notice("warn", "Set the recording date."));
    else if (!priors.bbox) warn.replaceChildren(notice("warn", "Set a search region."));
    else go("audio");
  }, "btn primary");
  const warn = h("div");

  container.append(
    h(
      "div",
      { class: "grid" },
      h("div", { class: "card" }, h("h2", {}, "1. Recording"), recordingPicker()),
      h("div", { class: "card" }, h("h2", {}, "2. Method"), methodBox),
    ),
    h("div", { class: "card", style: { marginTop: "16px" } }, h("h2", {}, "3. Priors"), priorsBox),
    h(
      "div",
      { class: "card", style: { marginTop: "16px" } },
      h("div", { class: "row" }, nextBtn, h("span", { class: "muted" }, "Settings are remembered on this computer.")),
      warn,
    ),
  );

  return () => {
    drawer?.disable();
    map?.remove();
    map = null;
  };
}
