import { describe, expect, it } from "vitest";
import { Vector3 } from "three";
import { Ellipsoid } from "3d-tiles-renderer";
import {
  MARS_RADIUS_M, SITES, angularDistance, formatCartographic, lonDelta, lonEast360, toRad,
} from "../src/geo";

describe("geo", () => {
  it("wraps longitudes to 0..360 east", () => {
    expect(lonEast360(-90)).toBe(270);
    expect(lonEast360(77.5)).toBe(77.5);
    expect(lonEast360(720)).toBe(0);
  });

  it("takes the short way across the antimeridian", () => {
    expect(lonDelta(350, 10)).toBe(20);
    expect(lonDelta(10, 350)).toBe(-20);
    expect(lonDelta(-170, 170)).toBe(-20);
  });

  it("measures great-circle angles", () => {
    expect(angularDistance({ lat: 0, lon: 0, height: 0 }, { lat: 0, lon: 90, height: 0 }))
      .toBeCloseTo(Math.PI / 2);
    expect(angularDistance({ lat: 90, lon: 0, height: 0 }, { lat: -90, lon: 0, height: 0 }))
      .toBeCloseTo(Math.PI);
  });

  it("formats lat, lon east and height", () => {
    expect(formatCartographic({ lat: 18.4, lon: 77.5, height: -2540.4 }))
      .toBe("18.4000°N  77.5000°E  -2540 m");
    expect(formatCartographic({ lat: -5.4, lon: -100, height: 120_000 }))
      .toBe("5.4000°S  260.0000°E  120.0 km");
  });

  it("lists Jezero at 18.4N 77.5E", () => {
    const jezero = SITES.find((s) => s.id === "jezero");
    expect(jezero).toMatchObject({ lat: 18.4, lon: 77.5 });
  });

  it("uses the IAU sphere in the renderer ellipsoid", () => {
    const e = new Ellipsoid(MARS_RADIUS_M, MARS_RADIUS_M, MARS_RADIUS_M);
    const p = e.getCartographicToPosition(toRad(18.4), toRad(77.5), 0, new Vector3());
    expect(p.length()).toBeCloseTo(MARS_RADIUS_M, 3);
    expect(p.z).toBeCloseTo(MARS_RADIUS_M * Math.sin(toRad(18.4)), 3);
    expect(Math.atan2(p.y, p.x)).toBeCloseTo(toRad(77.5), 9);
  });
});
