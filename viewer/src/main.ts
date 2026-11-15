import { Vector2 } from "three";
import "./style.css";
import { resolveCatalog, ResolvedLayer } from "./catalog";
import { SITES, Site, formatCartographic } from "./geo";
import { MarsScene } from "./scene";
import { parsePosesCsv } from "./traverse";
import { PanoEntry, PanoInfo, panoEntries } from "./pano";

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

function panoControls(mars: MarsScene): void {
  const canvas = mars.renderer.domElement;
  let last: { x: number; y: number } | null = null;
  canvas.addEventListener("pointerdown", (e) => { if (mars.inPano) last = { x: e.clientX, y: e.clientY }; });
  window.addEventListener("pointerup", () => { last = null; });
  canvas.addEventListener("pointermove", (e) => {
    if (!last || !mars.inPano) return;
    const k = mars.camera.fov / canvas.clientHeight;
    mars.lookPano(-(e.clientX - last.x) * k, (e.clientY - last.y) * k);
    last = { x: e.clientX, y: e.clientY };
  });
  canvas.addEventListener("wheel", (e) => { if (mars.inPano) mars.zoomPano(e.deltaY * 0.02); });
  window.addEventListener("keydown", (e) => { if (e.key === "Escape") exitStreetView(mars); });
}

function exitStreetView(mars: MarsScene): void {
  mars.exitPano();
  document.body.classList.remove("street");
}

async function openStreetView(mars: MarsScene, entry: PanoEntry): Promise<void> {
  const info = (await (await fetch(entry.url)).json()) as PanoInfo;
  await mars.enterPano(info, entry.url.slice(0, entry.url.lastIndexOf("/") + 1));
  document.body.classList.add("street");
}

function streetButtons(mars: MarsScene, parent: HTMLElement, layers: ResolvedLayer[]): void {
  const entries = layers.flatMap((l) => panoEntries(l.baseUrl, l.layer.assets));
  for (const entry of entries) {
    const button = el("button", { "data-stop": entry.stopId }, `Street view: ${entry.stopId}`);
    button.addEventListener("click", () => void openStreetView(mars, entry));
    parent.appendChild(button);
  }
  const exit = el("button", { class: "exit-street" }, "Exit street view (Esc)");
  exit.addEventListener("click", () => exitStreetView(mars));
  document.body.appendChild(exit);
}

function showLayers(list: HTMLElement, layers: ResolvedLayer[], errors: string[]): void {
  for (const l of layers) list.appendChild(el("li", {}, `${l.key} ${l.layer.version}`));
  for (const e of errors) list.appendChild(el("li", { class: "error" }, e));
}

async function addTraverse(mars: MarsScene, layer: ResolvedLayer): Promise<void> {
  const csv = layer.layer.assets.poses_csv;
  if (!csv) return;
  const res = await fetch(`${layer.baseUrl}${csv}`);
  if (res.ok) mars.addTraverse(parsePosesCsv(await res.text()));
}

async function loadLayers(mars: MarsScene, list: HTMLElement, street: HTMLElement): Promise<void> {
  try {
    const { layers, errors } = await resolveCatalog((u) => fetch(u), DATA_BASE);
    const detail = layers.filter((l) => TERRAIN_RANK[l.layer.layer] === 2)
      .map((l) => l.layer.bounds_lonlat);
    for (const l of layers) {
      const clip = TERRAIN_RANK[l.layer.layer] === 1 ? detail : [];
      for (const url of l.tilesetUrls) mars.addTileset(url, clip);
    }
    await Promise.all(layers.map((l) => addTraverse(mars, l)));
    streetButtons(mars, street, layers);
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
  panoControls(mars);
  mars.start();
  void loadLayers(mars, layers, street);
  Object.assign(window, { mars });
}

main();
