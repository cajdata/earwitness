// MapLibre helpers: a raster basemap with attribution, a two-click region drawer, and the
// heat-map layers for a run's results.

import maplibregl, { type GeoJSONSource, type MapMouseEvent, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection, Polygon } from "geojson";
import type { Bounds, HeatMap } from "./types";

export const DEFAULT_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILES_KEY = "earwitness.tiles";
const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors (ODbL)';

export function tileUrl(): string {
  return localStorage.getItem(TILES_KEY) ?? DEFAULT_TILES;
}

export function setTileUrl(url: string): void {
  if (url === DEFAULT_TILES) localStorage.removeItem(TILES_KEY);
  else localStorage.setItem(TILES_KEY, url);
}

export function baseStyle(): StyleSpecification {
  const tiles = tileUrl().trim();
  if (!tiles) {
    return {
      version: 8,
      sources: {},
      layers: [{ id: "background", type: "background", paint: { "background-color": "#e8edf2" } }],
    };
  }
  return {
    version: 8,
    sources: {
      basemap: { type: "raster", tiles: [tiles], tileSize: 256, attribution: OSM_ATTRIBUTION, maxzoom: 19 },
    },
    layers: [{ id: "basemap", type: "raster", source: "basemap" }],
  };
}

export function createMap(container: HTMLElement, center: [number, number] = [-95, 38], zoom = 3): maplibregl.Map {
  const map = new maplibregl.Map({ container, style: baseStyle(), center, zoom, attributionControl: false });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.AttributionControl({ compact: false }), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  return map;
}

export function bboxPolygon(b: Bounds): Feature<Polygon> {
  const [w, s, e, n] = b;
  return {
    type: "Feature",
    properties: {},
    geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
  };
}

function empty(): FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

/** Two clicks draw a rectangle: one corner, then the opposite one. */
export class BBoxDrawer {
  private map: maplibregl.Map;
  private onChange: (b: Bounds) => void;
  private corner: [number, number] | null = null;
  private active = false;
  private readonly click = (e: MapMouseEvent) => this.handleClick(e);
  private readonly move = (e: MapMouseEvent) => this.handleMove(e);

  constructor(map: maplibregl.Map, onChange: (b: Bounds) => void) {
    this.map = map;
    this.onChange = onChange;
    const add = () => {
      if (map.getSource("bbox")) return;
      map.addSource("bbox", { type: "geojson", data: empty() });
      map.addLayer({
        id: "bbox-fill",
        type: "fill",
        source: "bbox",
        paint: { "fill-color": "#1d4ed8", "fill-opacity": 0.12 },
      });
      map.addLayer({
        id: "bbox-line",
        type: "line",
        source: "bbox",
        paint: { "line-color": "#1d4ed8", "line-width": 2 },
      });
    };
    if (map.isStyleLoaded()) add();
    map.on("load", add);
    map.on("style.load", add);
  }

  enable(): void {
    this.active = true;
    this.corner = null;
    this.map.getCanvas().style.cursor = "crosshair";
    this.map.on("click", this.click);
    this.map.on("mousemove", this.move);
  }

  disable(): void {
    this.active = false;
    this.corner = null;
    this.map.getCanvas().style.cursor = "";
    this.map.off("click", this.click);
    this.map.off("mousemove", this.move);
  }

  get drawing(): boolean {
    return this.active;
  }

  set(b: Bounds | null): void {
    const source = this.map.getSource("bbox") as GeoJSONSource | undefined;
    if (!source) {
      this.map.once("load", () => this.set(b));
      return;
    }
    source.setData(b ? { type: "FeatureCollection", features: [bboxPolygon(b)] } : empty());
  }

  private static bounds(a: [number, number], b: [number, number]): Bounds {
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  }

  private handleClick(e: MapMouseEvent): void {
    const p: [number, number] = [e.lngLat.lng, e.lngLat.lat];
    if (!this.corner) {
      this.corner = p;
      return;
    }
    const b = BBoxDrawer.bounds(this.corner, p);
    this.disable();
    if (b[2] - b[0] < 1e-4 || b[3] - b[1] < 1e-4) return;
    this.set(b);
    this.onChange(b);
  }

  private handleMove(e: MapMouseEvent): void {
    if (!this.corner) return;
    this.set(BBoxDrawer.bounds(this.corner, [e.lngLat.lng, e.lngLat.lat]));
  }
}

export function fitBounds(map: maplibregl.Map, b: Bounds, padding = 40): void {
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding, duration: 400, maxZoom: 15 });
}

export function boundsOf(data: HeatMap): Bounds | null {
  if (!data.features.length) return null;
  let w = 180, s = 90, e = -180, n = -90;
  for (const f of data.features) {
    const [x, y] = f.geometry.coordinates;
    w = Math.min(w, x);
    e = Math.max(e, x);
    s = Math.min(s, y);
    n = Math.max(n, y);
  }
  const padX = Math.max((e - w) * 0.1, 0.002);
  const padY = Math.max((n - s) * 0.1, 0.002);
  return [w - padX, s - padY, e + padX, n + padY];
}

/** Score-coloured points for every scored cell, with the ranked finals drawn on top. */
export function showHeat(map: maplibregl.Map, data: HeatMap): void {
  const cells: HeatMap = { type: "FeatureCollection", features: data.features.filter((f) => f.properties.stage !== "final") };
  const finals: HeatMap = { type: "FeatureCollection", features: data.features.filter((f) => f.properties.stage === "final") };
  const scores = cells.features.map((f) => f.properties.score).filter((s) => Number.isFinite(s));
  const lo = scores.length ? Math.min(...scores) : 0;
  const hi = scores.length ? Math.max(...scores) : 1;
  const mid = lo + (hi - lo) * 0.6;
  const apply = () => {
    for (const id of ["heat-finals-label", "heat-finals", "heat-cells"]) if (map.getLayer(id)) map.removeLayer(id);
    for (const id of ["heat-cells", "heat-finals"]) if (map.getSource(id)) map.removeSource(id);
    map.addSource("heat-cells", { type: "geojson", data: cells });
    map.addSource("heat-finals", { type: "geojson", data: finals });
    map.addLayer({
      id: "heat-cells",
      type: "circle",
      source: "heat-cells",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 2, 8, 5, 12, 9, 16, 16],
        "circle-color": [
          "interpolate",
          ["linear"],
          ["get", "score"],
          lo,
          "#2c7bb6",
          mid,
          "#ffffbf",
          hi === lo ? hi + 1e-9 : hi,
          "#d7191c",
        ],
        "circle-opacity": 0.75,
        "circle-stroke-width": 0,
      },
    });
    map.addLayer({
      id: "heat-finals",
      type: "circle",
      source: "heat-finals",
      paint: {
        "circle-radius": 9,
        "circle-color": "#111827",
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2,
      },
    });
    map.addLayer({
      id: "heat-finals-label",
      type: "symbol",
      source: "heat-finals",
      layout: {
        "text-field": ["to-string", ["get", "rank"]],
        "text-size": 11,
        "text-allow-overlap": true,
        "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
      },
      paint: { "text-color": "#ffffff" },
    });
  };
  if (map.isStyleLoaded()) apply();
  else map.once("load", apply);
}

export function marker(map: maplibregl.Map, lon: number, lat: number, color = "#16a34a"): maplibregl.Marker {
  return new maplibregl.Marker({ color }).setLngLat([lon, lat]).addTo(map);
}
