import { Vector2 } from "three";
import type { TilesRenderer } from "3d-tiles-renderer";
import "./style.css";
import { resolveCatalog, ResolvedLayer } from "./catalog";
import { SITES, Site, formatCartographic } from "./geo";
import { MarsScene } from "./scene";
import { parsePosesCsv } from "./traverse";
import { StreetView, loadStops } from "./street";
import { fetchFresh } from "./catalog";

const TERRAIN_RANK: Record<string, number> = { l1_global: 1, l2_regional: 2 };
const DATA_BASE: string = import.meta.env.VITE_DATA_BASE ?? "";

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, attrs: Record<string, string> = {}, text = "",
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.textContent = text;
  return node;
}

function siteButtons(mars: MarsScene, parent: HTMLElement): void {
  for (const site of SITES) {
    const button = el("button", { "data-site": site.id }, site.name);
    button.addEventListener("click", () => flyToSite(mars, site));
    parent.appendChild(button);
  }
}

function flyToSite(mars: MarsScene, site: Site): void {
  mars.flyTo({ lat: site.lat, lon: site.lon, height: site.viewHeight });
}

function cursorReadout(mars: MarsScene, readout: HTMLElement): void {
  const ndc = new Vector2();
  let pending = false;
  mars.renderer.domElement.addEventListener("pointermove", (e) => {
    const rect = mars.renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const c = mars.pick(ndc);
      readout.textContent = c ? formatCartographic(c) : "—";
    });
  });
}

function streetPanel(street: StreetView, parent: HTMLElement): void {
  const stops = street.panoStops;
  if (!stops.length) return;
  const select = el("select", { class: "stop-select" });
  for (const s of stops) select.appendChild(el("option", { value: s.id }, s.id.replace("m2020_", "")));
  const open = el("button", { "data-action": "street" }, `Street view (${stops.length} stops)`);
  open.addEventListener("click", () => void street.open(select.value));
  const walk = el("button", { "data-action": "walk" }, "Walk here");
  walk.addEventListener("click", () => {
    const s = stops.find((x) => x.id === select.value) ?? stops[0];
    street.walk({ lat: s.lat, lon: s.lon, height: s.height });
  });
  const exit = el("button", { class: "exit-street" }, "Exit (Esc)");
  exit.addEventListener("click", () => street.exit());
  parent.append(select, open, walk);
  document.body.appendChild(exit);
}

function showLayers(list: HTMLElement, layers: ResolvedLayer[], errors: string[]): void {
  for (const l of layers) list.appendChild(el("li", {}, `${l.key} ${l.layer.version}`));
  for (const e of errors) list.appendChild(el("li", { class: "error" }, e));
}

async function addTraverse(mars: MarsScene, layer: ResolvedLayer): Promise<void> {
  const csv = layer.layer.assets.poses_csv;
  if (!csv) return;
  const res = await fetchFresh(`${layer.baseUrl}${csv}`);
  if (res.ok) mars.addTraverse(parsePosesCsv(await res.text()));
}

async function loadLayers(
  mars: MarsScene, street: StreetView, list: HTMLElement, panel: HTMLElement,
): Promise<void> {
  try {
    const { layers, errors } = await resolveCatalog(fetchFresh, DATA_BASE);
    const detail = layers.filter((l) => TERRAIN_RANK[l.layer.layer] === 2)
      .map((l) => l.layer.bounds_lonlat);
    const terrain: { rank: number; tiles: TilesRenderer }[] = [];
    for (const l of layers) {
      const rank = TERRAIN_RANK[l.layer.layer];
      const clip = rank === 1 ? detail : [];
      for (const url of l.tilesetUrls) {
        const tiles = mars.addTileset(url, clip);
        if (rank) terrain.push({ rank, tiles });
      }
    }
    mars.setTerrain(terrain.sort((a, b) => b.rank - a.rank).map((t) => t.tiles));
    await Promise.all(layers.map((l) => addTraverse(mars, l)));
    street.setStops(await loadStops(layers));
    streetPanel(street, panel);
    showLayers(list, layers, errors);
  } catch (err) {
    list.appendChild(el("li", { class: "error" }, String(err)));
  }
}

function ui(): { sites: HTMLElement; readout: HTMLElement; layers: HTMLElement; street: HTMLElement } {
  const panel = el("div", { class: "panel" });
  const sites = el("div", { class: "sites" });
  const street = el("div", { class: "sites street-list" });
  const layers = el("ul", { class: "layers" });
  const readout = el("div", { class: "readout" }, "—");
  panel.append(el("h1", {}, "Mars Street View"), sites, street, layers);
  document.body.append(panel, readout);
  return { sites, readout, layers, street };
}

function main(): void {
  const mars = new MarsScene(document.body);
  const { sites, readout, layers, street } = ui();
  siteButtons(mars, sites);
  cursorReadout(mars, readout);
  const streetView = new StreetView(mars);
  mars.start();
  void loadLayers(mars, streetView, layers, street);
  Object.assign(window, { mars, streetView });
}

main();
