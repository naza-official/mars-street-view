import { describe, expect, it } from "vitest";
import {
  CostGrid, GridMeta, astar, cellCost, cellLonLat, describeRoute, eqcForward, eqcInverse,
  lonLatToCell, parseGrid,
} from "../src/pathfind";

const META: GridMeta = {
  name: "t", bin: "t.bin", width: 5, height: 5, cell_m: 4, x0: -10, y1: 10,
  eqc: { lon_0: 77.5, lat_0: 18.4, lat_ts: 18.4, radius_m: 3396190 },
  slope_scale: 4, height_offset_m: 10000, height_scale: 10,
  classes: ["soil", "bedrock", "sand", "big_rock", "unknown"],
  cost: { soil: 1, bedrock: 1.5, sand: 3, big_rock: 8, unknown: 2.5 }, max_slope_deg: 25,
};

function grid(cls: number[], slopeDeg: number[] = cls.map(() => 0)): CostGrid {
  const n = cls.length;
  const buf = new ArrayBuffer(4 * n);
  const bytes = new Uint8Array(buf);
  bytes.set(cls, 0);
  bytes.set(slopeDeg.map((s) => s * 4), n);
  const view = new DataView(buf, 2 * n, 2 * n);
  for (let i = 0; i < n; i++) view.setUint16(2 * i, (10000 - 4250) * 10, true);
  return parseGrid(META, buf);
}

describe("pathfind", () => {
  it("round-trips the site projection", () => {
    const [x, y] = eqcForward(META, 77.45, 18.44);
    const [lon, lat] = eqcInverse(META, x, y);
    expect(lon).toBeCloseTo(77.45, 9);
    expect(lat).toBeCloseTo(18.44, 9);
  });

  it("matches B's cost model", () => {
    expect(cellCost(0, 0, META)).toBe(1);
    expect(cellCost(2, 10, META)).toBe(6);
    expect(cellCost(3, 30, META)).toBe(Infinity);
    expect(cellCost(255, 0, META)).toBe(Infinity);
  });

  it("decodes heights and maps lon/lat to cells", () => {
    const g = grid(new Array(25).fill(0));
    expect(g.height[0]).toBeCloseTo(-4250, 3);
    const [lon, lat] = cellLonLat(g, 12);
    expect(lonLatToCell(g, lon, lat)).toEqual({ row: 2, col: 2 });
  });

  it("routes around big rock and blocked slopes", () => {
    const B = 3;
    const g = grid([
      0, 0, 0, 0, 0,
      0, B, B, B, 0,
      0, B, B, B, 0,
      0, B, B, B, 0,
      0, 0, 0, 0, 0,
    ]);
    const path = astar(g, { row: 2, col: 0 }, { row: 2, col: 4 });
    expect(path).not.toBeNull();
    const route = describeRoute(g, path as number[], { row: 2, col: 0 }, { row: 2, col: 4 });
    expect(route.share.big_rock).toBe(0);
    expect(route.lengthM).toBeGreaterThan(route.straightM);
    const walled = grid(new Array(25).fill(0), [0, 0, 40, 0, 0, 0, 0, 40, 0, 0, 0, 0, 40, 0, 0,
      0, 0, 40, 0, 0, 0, 0, 40, 0, 0]);
    expect(astar(walled, { row: 2, col: 0 }, { row: 2, col: 4 })).toBeNull();
  });

  it("takes the straight line on uniform soil", () => {
    const g = grid(new Array(25).fill(0));
    const path = astar(g, { row: 0, col: 0 }, { row: 4, col: 4 }) as number[];
    expect(path).toEqual([0, 6, 12, 18, 24]);
  });
});
