import { Cartographic, MARS_RADIUS_M, angularDistance, lonDelta } from "./geo";

export const easeInOut = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

export function flightDuration(from: Cartographic, to: Cartographic): number {
  const arc = angularDistance(from, to);
  return Math.min(4, 1.2 + arc * 1.5);
}

function arcHeight(from: Cartographic, to: Cartographic): number {
  const ground = angularDistance(from, to) * MARS_RADIUS_M;
  return Math.max(0, ground * 0.35 - Math.max(from.height, to.height));
}

export function flightPoint(from: Cartographic, to: Cartographic, t: number): Cartographic {
  const e = easeInOut(Math.min(1, Math.max(0, t)));
  const bump = 4 * e * (1 - e) * arcHeight(from, to);
  return {
    lat: from.lat + (to.lat - from.lat) * e,
    lon: from.lon + lonDelta(from.lon, to.lon) * e,
    height: from.height + (to.height - from.height) * e + bump,
  };
}
