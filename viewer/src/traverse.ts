export interface TraversePoint {
  lon: number;
  lat: number;
  height: number;
}

export function parsePosesCsv(text: string): TraversePoint[] {
  const [header, ...rows] = text.trim().split(/\r?\n/);
  const cols = header.split(",");
  const [iLon, iLat, iH] = ["lon", "lat", "height"].map((c) => cols.indexOf(c));
  if (Math.min(iLon, iLat, iH) < 0) throw new Error("poses csv needs lon, lat, height");
  return rows
    .map((r) => r.split(","))
    .map((f) => ({ lon: Number(f[iLon]), lat: Number(f[iLat]), height: Number(f[iH]) }))
    .filter((p) => Number.isFinite(p.lon) && Number.isFinite(p.lat) && Number.isFinite(p.height));
}
