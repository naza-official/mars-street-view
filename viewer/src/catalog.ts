export interface LayerJson {
  layer: string;
  site: string;
  version: string;
  schema: string;
  owner: "A" | "B";
  bounds_lonlat: [number, number, number, number];
  assets: Record<string, string>;
  notes?: string;
}

export interface Catalog {
  schema: string;
  layers: Record<string, string>;
}

export interface ResolvedLayer {
  key: string;
  baseUrl: string;
  layer: LayerJson;
  tilesetUrls: string[];
}

export type Fetcher = (url: string) => Promise<Response>;

export const fetchFresh: Fetcher = (url) => fetch(url, { cache: "no-cache" });

const ORBITAL_LAYERS = new Set(["l1_global", "l2_regional", "l5_thermal"]);

export function layerPrefix(layer: string): "orbital" | "rover" {
  return ORBITAL_LAYERS.has(layer) ? "orbital" : "rover";
}

export function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

export function layerBaseUrl(dataBase: string, key: string, version: string): string {
  const [layer, site] = key.split("/");
  if (!layer || !site) throw new Error(`bad catalog key: ${key}`);
  return joinUrl(dataBase, `${layerPrefix(layer)}/${layer}/${site}/${version}/`);
}

async function getJson<T>(fetcher: Fetcher, url: string): Promise<T> {
  const res = await fetcher(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

export async function loadCatalog(fetcher: Fetcher, dataBase: string): Promise<Catalog> {
  const catalog = await getJson<Catalog>(fetcher, joinUrl(dataBase, "catalog/index.json"));
  if (catalog.schema !== "1.0") throw new Error(`unsupported catalog schema ${catalog.schema}`);
  return catalog;
}

export function tilesetAssets(layer: LayerJson): string[] {
  return Object.entries(layer.assets)
    .filter(([name]) => name === "tileset" || name.endsWith("_tileset"))
    .map(([, path]) => path);
}

export async function resolveLayer(
  fetcher: Fetcher, dataBase: string, key: string, version: string,
): Promise<ResolvedLayer> {
  const baseUrl = layerBaseUrl(dataBase, key, version);
  const layer = await getJson<LayerJson>(fetcher, joinUrl(baseUrl, "layer.json"));
  if (layer.schema !== "1.0") throw new Error(`${key}: unsupported layer schema ${layer.schema}`);
  return { key, baseUrl, layer, tilesetUrls: tilesetAssets(layer).map((a) => joinUrl(baseUrl, a)) };
}

export interface CatalogResult {
  layers: ResolvedLayer[];
  errors: string[];
}

export async function resolveCatalog(fetcher: Fetcher, dataBase: string): Promise<CatalogResult> {
  const catalog = await loadCatalog(fetcher, dataBase);
  const entries = Object.entries(catalog.layers).filter(([key]) => !key.startsWith("_"));
  const results = await Promise.allSettled(
    entries.map(([key, version]) => resolveLayer(fetcher, dataBase, key, version)),
  );
  return {
    layers: results.flatMap((r) => (r.status === "fulfilled" ? [r.value] : [])),
    errors: results.flatMap((r) => (r.status === "rejected" ? [String(r.reason)] : [])),
  };
}
