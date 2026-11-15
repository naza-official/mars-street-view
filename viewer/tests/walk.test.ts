import { describe, expect, it } from "vitest";
import { bearingDistance, nearbyStops, offsetCartographic, step } from "../src/walk";

const OEB = { lat: 18.444627, lon: 77.450886, height: -4252.8 };

describe("walk", () => {
  it("steps north and east in metres", () => {
    const n = step(OEB, 0, { forward: 1, right: 0, run: false }, 5);
    expect(bearingDistance(OEB, n).dist).toBeCloseTo(10, 3);
    expect(bearingDistance(OEB, n).az).toBeCloseTo(0, 3);
    const e = step(OEB, 0, { forward: 0, right: 1, run: false }, 5);
    expect(bearingDistance(OEB, e).az).toBeCloseTo(90, 3);
  });

  it("measures bearing clockwise from north", () => {
    const sw = offsetCartographic(OEB, -100, -100);
    const r = bearingDistance(OEB, sw);
    expect(r.az).toBeCloseTo(225, 3);
    expect(r.dist).toBeCloseTo(141.42, 1);
  });

  it("lists nearby stops with panoramas, nearest first", () => {
    const stops = [
      { id: "a", ...offsetCartographic(OEB, 300, 0), panoUrl: "a.json" },
      { id: "b", ...offsetCartographic(OEB, 50, 0), panoUrl: "b.json" },
      { id: "c", ...offsetCartographic(OEB, 10, 0), panoUrl: null },
      { id: "d", ...offsetCartographic(OEB, 900, 0), panoUrl: "d.json" },
    ];
    expect(nearbyStops(OEB, stops, 400).map((s) => s.stop.id)).toEqual(["b", "a"]);
    expect(nearbyStops(OEB, stops, 400, "b").map((s) => s.stop.id)).toEqual(["a"]);
  });
});
