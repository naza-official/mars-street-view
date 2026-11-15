import { describe, expect, it } from "vitest";
import { parsePosesCsv } from "../src/traverse";

describe("traverse", () => {
  it("parses the L3 poses csv", () => {
    const csv = "site,drive,lon,lat,height,roll\n1,0,77.45,18.44,-4252.8,0\n1,52,77.46,18.45,-4250,0\nbad,row,x,y,z,0\n";
    expect(parsePosesCsv(csv)).toEqual([
      { lon: 77.45, lat: 18.44, height: -4252.8 },
      { lon: 77.46, lat: 18.45, height: -4250 },
    ]);
  });

  it("rejects a csv without coordinates", () => {
    expect(() => parsePosesCsv("a,b\n1,2")).toThrow();
  });
});
