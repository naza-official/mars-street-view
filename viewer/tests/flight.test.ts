import { describe, expect, it } from "vitest";
import { easeInOut, flightDuration, flightPoint } from "../src/flight";

const space = { lat: 0, lon: 0, height: 10_000_000 };
const jezero = { lat: 18.4, lon: 77.5, height: 120_000 };

describe("flight", () => {
  it("eases from 0 to 1", () => {
    expect(easeInOut(0)).toBe(0);
    expect(easeInOut(1)).toBe(1);
    expect(easeInOut(0.5)).toBeCloseTo(0.5);
  });

  it("starts and ends on the endpoints", () => {
    expect(flightPoint(space, jezero, 0)).toEqual(space);
    expect(flightPoint(space, jezero, 1)).toEqual(jezero);
    expect(flightPoint(space, jezero, 2)).toEqual(jezero);
  });

  it("crosses 0E the short way", () => {
    const mid = flightPoint({ lat: 0, lon: 350, height: 1e5 }, { lat: 0, lon: 10, height: 1e5 }, 0.5);
    expect(mid.lon).toBeCloseTo(360);
  });

  it("climbs between two low points far apart", () => {
    const a = { lat: 18.4, lon: 77.5, height: 120_000 };
    const b = { lat: -5.4, lon: 137.8, height: 120_000 };
    expect(flightPoint(a, b, 0.5).height).toBeGreaterThan(1_000_000);
  });

  it("keeps durations bounded", () => {
    expect(flightDuration(jezero, jezero)).toBeCloseTo(1.2);
    expect(flightDuration({ lat: 90, lon: 0, height: 0 }, { lat: -90, lon: 0, height: 0 })).toBe(4);
  });
});
