import { describe, expect, it } from "vitest";
import { Fetcher, layerBaseUrl, layerPrefix, resolveCatalog } from "../src/catalog";

const L1 = {
  layer: "l1_global", site: "global", version: "v2", schema: "1.0", owner: "A",
  bounds_lonlat: [0, -90, 360, 90], assets: { dtm: "dtm.tif", tileset: "tileset.json" },
};

function fakeFetch(files: Record<string, unknown>): { fetcher: Fetcher; calls: string[] } {
  const calls: string[] = [];
  const fetcher: Fetcher = async (url) => {
    calls.push(url);
    return url in files
      ? new Response(JSON.stringify(files[url]), { status: 200 })
      : new Response("missing", { status: 404 });
  };
  return { fetcher, calls };
}

describe("catalog", () => {
  it("maps layers to lane prefixes", () => {
    expect(layerPrefix("l1_global")).toBe("orbital");
    expect(layerPrefix("l2_regional")).toBe("orbital");
    expect(layerPrefix("l3_poses")).toBe("rover");
  });

  it("builds version folder urls", () => {
    expect(layerBaseUrl("", "l1_global/global", "v2")).toBe("/orbital/l1_global/global/v2/");
    expect(layerBaseUrl("https://cdn.example/", "l4_streetview/jezero", "v1"))
      .toBe("https://cdn.example/rover/l4_streetview/jezero/v1/");
    expect(() => layerBaseUrl("", "l1_global", "v1")).toThrow();
  });

  it("loads the catalog, then layer.json, then the tileset url", async () => {
    const { fetcher, calls } = fakeFetch({
      "/catalog/index.json": { schema: "1.0", layers: { "l1_global/global": "v2", "_smoke/global": "v1" } },
      "/orbital/l1_global/global/v2/layer.json": L1,
    });
    const { layers, errors } = await resolveCatalog(fetcher, "");
    expect(calls).toEqual(["/catalog/index.json", "/orbital/l1_global/global/v2/layer.json"]);
    expect(errors).toEqual([]);
    expect(layers).toHaveLength(1);
    expect(layers[0].tilesetUrl).toBe("/orbital/l1_global/global/v2/tileset.json");
  });

  it("reports a missing layer without dropping the others", async () => {
    const { fetcher } = fakeFetch({
      "/catalog/index.json": { schema: "1.0", layers: { "l1_global/global": "v2", "l2_regional/jezero": "v1" } },
      "/orbital/l1_global/global/v2/layer.json": L1,
    });
    const { layers, errors } = await resolveCatalog(fetcher, "");
    expect(layers.map((l) => l.key)).toEqual(["l1_global/global"]);
    expect(errors[0]).toContain("l2_regional/jezero/v1/layer.json: HTTP 404");
  });

  it("rejects an unknown catalog schema", async () => {
    const { fetcher } = fakeFetch({ "/catalog/index.json": { schema: "2.0", layers: {} } });
    await expect(resolveCatalog(fetcher, "")).rejects.toThrow("unsupported catalog schema");
  });
});
