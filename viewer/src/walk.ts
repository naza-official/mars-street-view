import { Cartographic, MARS_RADIUS_M, toDeg, toRad } from "./geo";

export const EYE_HEIGHT_M = 1.8;
export const WALK_SPEED_MS = 2;
export const RUN_SPEED_MS = 12;
export const ENTER_PANO_RADIUS_M = 20;
export const ARROW_RANGE_M = 400;

export interface StopPoint {
  id: string;
  lat: number;
  lon: number;
  height: number;
  panoUrl: string | null;
}

export interface MoveInput {
  forward: number;
  right: number;
  run: boolean;
}

export function offsetCartographic(at: Cartographic, north: number, east: number): Cartographic {
  const lat = at.lat + toDeg(north / MARS_RADIUS_M);
  const lon = at.lon + toDeg(east / (MARS_RADIUS_M * Math.cos(toRad(at.lat))));
  return { lat, lon, height: at.height };
}

export function step(at: Cartographic, azDeg: number, input: MoveInput, dt: number): Cartographic {
  const speed = (input.run ? RUN_SPEED_MS : WALK_SPEED_MS) * dt;
  const az = toRad(azDeg);
  const north = (Math.cos(az) * input.forward - Math.sin(az) * input.right) * speed;
  const east = (Math.sin(az) * input.forward + Math.cos(az) * input.right) * speed;
  return offsetCartographic(at, north, east);
}

export function localOffset(from: Cartographic, to: Cartographic): { north: number; east: number } {
  const north = toRad(to.lat - from.lat) * MARS_RADIUS_M;
  const east = toRad(to.lon - from.lon) * MARS_RADIUS_M * Math.cos(toRad(from.lat));
  return { north, east };
}

export function bearingDistance(from: Cartographic, to: Cartographic): { az: number; dist: number } {
  const { north, east } = localOffset(from, to);
  return { az: (toDeg(Math.atan2(east, north)) + 360) % 360, dist: Math.hypot(north, east) };
}

export function nearbyStops(at: Cartographic, stops: StopPoint[], range: number, skip?: string) {
  return stops
    .filter((s) => s.id !== skip && s.panoUrl)
    .map((s) => ({ stop: s, ...bearingDistance(at, s) }))
    .filter((s) => s.dist <= range)
    .sort((a, b) => a.dist - b.dist);
}
