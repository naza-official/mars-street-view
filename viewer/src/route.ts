export type RoutePoint = [number, number, number, number];

export interface RouteData {
  name: string;
  legend: Record<string, [string, string]>;
  cell_m: number;
  route: RoutePoint[];
  cells: RoutePoint[];
  summary?: Record<string, unknown>;
}

export function routeAssets(assets: Record<string, string>): string[] {
  return Object.entries(assets).filter(([k]) => k.endsWith("_route")).map(([, v]) => v);
}

export function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.replace("#", ""), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function classColor(legend: RouteData["legend"], cls: number): [number, number, number] {
  const entry = legend[String(cls)] ?? legend["4"];
  return entry ? hexToRgb(entry[1]) : [0.5, 0.5, 0.5];
}

export function routeLength(points: RoutePoint[], metresPerDeg: number): number {
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    const [lo0, la0] = points[i - 1];
    const [lo1, la1] = points[i];
    const dx = (lo1 - lo0) * metresPerDeg * Math.cos(((la0 + la1) / 2) * Math.PI / 180);
    const dy = (la1 - la0) * metresPerDeg;
    total += Math.hypot(dx, dy);
  }
  return total;
}
