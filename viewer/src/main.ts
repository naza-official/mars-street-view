import { Vector2 } from "three";
import "./style.css";
import { resolveCatalog, ResolvedLayer } from "./catalog";
import { SITES, Site, formatCartographic } from "./geo";
import { MarsScene } from "./scene";

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

function showLayers(list: HTMLElement, layers: ResolvedLayer[], errors: string[]): void {
  for (const l of layers) list.appendChild(el("li", {}, `${l.key} ${l.layer.version}`));
  for (const e of errors) list.appendChild(el("li", { class: "error" }, e));
}

async function loadLayers(mars: MarsScene, list: HTMLElement): Promise<void> {
  try {
    const { layers, errors } = await resolveCatalog((u) => fetch(u), DATA_BASE);
    for (const l of layers) if (l.tilesetUrl) mars.addTileset(l.tilesetUrl);
    showLayers(list, layers, errors);
  } catch (err) {
    list.appendChild(el("li", { class: "error" }, String(err)));
  }
}

function ui(): { sites: HTMLElement; readout: HTMLElement; layers: HTMLElement } {
  const panel = el("div", { class: "panel" });
  const sites = el("div", { class: "sites" });
  const layers = el("ul", { class: "layers" });
  const readout = el("div", { class: "readout" }, "—");
  panel.append(el("h1", {}, "Mars Street View"), sites, layers);
  document.body.append(panel, readout);
  return { sites, readout, layers };
}

function main(): void {
  const mars = new MarsScene(document.body);
  const { sites, readout, layers } = ui();
  siteButtons(mars, sites);
  cursorReadout(mars, readout);
  mars.start();
  void loadLayers(mars, layers);
  Object.assign(window, { mars });
}

main();
