import { describe, expect, it } from "vitest";
import { classColor, hexToRgb, routeAssets, routeLength } from "../src/route";

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

  it("measures route length on the sphere", () => {
    const mpd = 3396190 * Math.PI / 180;
    expect(routeLength([[77.45, 18.44, 0, 0], [77.45, 18.45, 0, 0]], mpd)).toBeCloseTo(592.7, 0);
  });
});
