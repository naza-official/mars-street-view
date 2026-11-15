import { describe, expect, it } from "vitest";
import { PANO_RADIUS_M, enuDirection, panoEntries, panoGeometry } from "../src/pano";

describe("pano", () => {
  it("maps azimuth clockwise from north in ENU", () => {
    const [e, n, u] = enuDirection(90, 0);
    expect([e, n, u].map((v) => Math.round(v * 1e9) / 1e9)).toEqual([1, 0, 0]);
    expect(enuDirection(0, 0)[1]).toBeCloseTo(1);
    expect(enuDirection(0, 90)[2]).toBeCloseTo(1);
  });

  it("puts equirect column 0 at north and the top row at the zenith", () => {
    const g = panoGeometry(0, 4, 2);
    const pos = g.getAttribute("position");
    const uv = g.getAttribute("uv");
    expect(uv.getX(0)).toBe(0);
    expect(pos.getY(5 + 0) / PANO_RADIUS_M).toBeCloseTo(1);
    const top = 2 * 5;
    expect(uv.getY(top)).toBe(1);
    expect(pos.getZ(top) / PANO_RADIUS_M).toBeCloseTo(1);
  });

  it("finds per-stop panoramas in layer assets", () => {
    expect(panoEntries("/rover/l4/jezero/v1/", { s1_pano: "s1/pano/pano.json", s1_stop: "x" }))
      .toEqual([{ stopId: "s1", url: "/rover/l4/jezero/v1/s1/pano/pano.json" }]);
  });
});
