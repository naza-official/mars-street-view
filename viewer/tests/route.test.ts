import { describe, expect, it } from "vitest";
import {
  classColor, classMapAssets, gridIndex, hexToRgb, routeAssets, routeLength,
} from "../src/route";

const LEGEND = { "0": ["soil", "#D7A05A"], "2": ["sand", "#FADC28"], "4": ["unknown", "#5A5A5A"] } as Record<string, [string, string]>;

describe("route", () => {
  it("finds route assets", () => {
    expect(routeAssets({ a_route: "a.json", a_geojson: "a.geojson", b_route: "b.json" }))
      .toEqual(["a.json", "b.json"]);
  });

  it("converts legend colours", () => {
    expect(hexToRgb("#FF0080")).toEqual([1, 0, 128 / 255]);
    expect(classColor(LEGEND, 2)).toEqual(hexToRgb("#FADC28"));
    expect(classColor(LEGEND, 9)).toEqual(hexToRgb("#5A5A5A"));
  });

  it("finds class-map assets and indexes a vertex grid", () => {
    expect(classMapAssets({ area_classmap: "area.json", area_classmap_png: "area.png" }))
      .toEqual(["area.json"]);
    expect(gridIndex(3, 2)).toEqual([0, 3, 1, 1, 3, 4, 1, 4, 2, 2, 4, 5]);
  });

  it("measures route length on the sphere", () => {
    const mpd = 3396190 * Math.PI / 180;
    expect(routeLength([[77.45, 18.44, 0, 0], [77.45, 18.45, 0, 0]], mpd)).toBeCloseTo(592.7, 0);
  });
});
