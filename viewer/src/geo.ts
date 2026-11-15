export const MARS_RADIUS_M = 3_396_190;

export interface Cartographic {
  lat: number;
  lon: number;
  height: number;
}

export interface Site {
  id: string;
  name: string;
  lat: number;
  lon: number;
  viewHeight: number;
}

export const SITES: Site[] = [
  { id: "jezero", name: "Jezero", lat: 18.4, lon: 77.5, viewHeight: 120_000 },
  { id: "oeb", name: "OEB landing", lat: 18.4446, lon: 77.4509, viewHeight: 3_000 },
  { id: "gale", name: "Gale", lat: -5.4, lon: 137.8, viewHeight: 250_000 },
];

export const toRad = (deg: number): number => (deg * Math.PI) / 180;
export const toDeg = (rad: number): number => (rad * 180) / Math.PI;

export function lonEast360(lonDeg: number): number {
  const lon = lonDeg % 360;
  return lon < 0 ? lon + 360 : lon;
}

export function lonDelta(fromDeg: number, toDegValue: number): number {
  const d = (toDegValue - fromDeg) % 360;
  if (d > 180) return d - 360;
  if (d < -180) return d + 360;
  return d;
}

export function angularDistance(a: Cartographic, b: Cartographic): number {
  const [la, lb] = [toRad(a.lat), toRad(b.lat)];
  const dLon = toRad(b.lon - a.lon);
  const c = Math.sin(la) * Math.sin(lb) + Math.cos(la) * Math.cos(lb) * Math.cos(dLon);
  return Math.acos(Math.min(1, Math.max(-1, c)));
}

function formatHeight(m: number): string {
  return Math.abs(m) >= 10_000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}

export function formatCartographic(c: Cartographic): string {
  const lat = `${Math.abs(c.lat).toFixed(4)}°${c.lat >= 0 ? "N" : "S"}`;
  const lon = `${lonEast360(c.lon).toFixed(4)}°E`;
  return `${lat}  ${lon}  ${formatHeight(c.height)}`;
}
