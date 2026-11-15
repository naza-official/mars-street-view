import type { RoutePoint } from "./route";

export interface GridMeta {
  name: string;
  bin: string;
  width: number;
  height: number;
  cell_m: number;
  x0: number;
  y1: number;
  eqc: { lon_0: number; lat_0: number; lat_ts: number; radius_m: number };
  slope_scale: number;
  height_offset_m: number;
  height_scale: number;
  classes: string[];
  cost: Record<string, number>;
  max_slope_deg: number;
}

export interface CostGrid {
  meta: GridMeta;
  cls: Uint8Array;
  slope: Float32Array;
  height: Float32Array;
  cost: Float32Array;
}

export interface Cell {
  row: number;
  col: number;
}

export interface PlannedRoute {
  cells: number[];
  lengthM: number;
  straightM: number;
  share: Record<string, number>;
  points: RoutePoint[];
}

const RAD = Math.PI / 180;

export function eqcForward(m: GridMeta, lon: number, lat: number): [number, number] {
  const { lon_0, lat_0, lat_ts, radius_m: r } = m.eqc;
  return [r * (lon - lon_0) * RAD * Math.cos(lat_ts * RAD), r * (lat - lat_0) * RAD];
}

export function eqcInverse(m: GridMeta, x: number, y: number): [number, number] {
  const { lon_0, lat_0, lat_ts, radius_m: r } = m.eqc;
  return [lon_0 + x / (r * Math.cos(lat_ts * RAD)) / RAD, lat_0 + y / r / RAD];
}

export function cellCost(cls: number, slopeDeg: number, m: GridMeta): number {
  const name = m.classes[cls];
  if (name === undefined || slopeDeg > m.max_slope_deg) return Infinity;
  return m.cost[name] * (1 + (slopeDeg / 10) ** 2);
}

export function parseGrid(meta: GridMeta, buf: ArrayBuffer): CostGrid {
  const n = meta.width * meta.height;
  const bytes = new Uint8Array(buf);
  const cls = bytes.slice(0, n);
  const slope = Float32Array.from(bytes.subarray(n, 2 * n), (v) => v / meta.slope_scale);
  const view = new DataView(buf, 2 * n, 2 * n);
  const height = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    height[i] = view.getUint16(2 * i, true) / meta.height_scale - meta.height_offset_m;
  }
  const cost = Float32Array.from(cls, (c, i) => cellCost(c, slope[i], meta));
  return { meta, cls, slope, height, cost };
}

export function lonLatToCell(g: CostGrid, lon: number, lat: number): Cell | null {
  const [x, y] = eqcForward(g.meta, lon, lat);
  const col = Math.floor((x - g.meta.x0) / g.meta.cell_m);
  const row = Math.floor((g.meta.y1 - y) / g.meta.cell_m);
  if (row < 0 || col < 0 || row >= g.meta.height || col >= g.meta.width) return null;
  return { row, col };
}

export function cellLonLat(g: CostGrid, idx: number): [number, number] {
  const row = Math.floor(idx / g.meta.width);
  const col = idx % g.meta.width;
  const x = g.meta.x0 + (col + 0.5) * g.meta.cell_m;
  const y = g.meta.y1 - (row + 0.5) * g.meta.cell_m;
  return eqcInverse(g.meta, x, y);
}

class MinHeap {
  private keys: number[] = [];
  private vals: number[] = [];

  get size(): number {
    return this.keys.length;
  }

  push(key: number, val: number): void {
    this.keys.push(key);
    this.vals.push(val);
    let i = this.keys.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.keys[p] <= key) break;
      this.swap(i, p);
      i = p;
    }
  }

  pop(): number {
    const top = this.vals[0];
    const lastK = this.keys.pop() as number;
    const lastV = this.vals.pop() as number;
    if (this.keys.length) {
      this.keys[0] = lastK;
      this.vals[0] = lastV;
      this.sink(0);
    }
    return top;
  }

  private sink(i: number): void {
    const n = this.keys.length;
    for (;;) {
      const l = 2 * i + 1;
      const r = l + 1;
      let m = i;
      if (l < n && this.keys[l] < this.keys[m]) m = l;
      if (r < n && this.keys[r] < this.keys[m]) m = r;
      if (m === i) return;
      this.swap(i, m);
      i = m;
    }
  }

  private swap(a: number, b: number): void {
    [this.keys[a], this.keys[b]] = [this.keys[b], this.keys[a]];
    [this.vals[a], this.vals[b]] = [this.vals[b], this.vals[a]];
  }
}

const STEPS: [number, number, number][] = [
  [-1, 0, 1], [1, 0, 1], [0, -1, 1], [0, 1, 1],
  [-1, -1, Math.SQRT2], [-1, 1, Math.SQRT2], [1, -1, Math.SQRT2], [1, 1, Math.SQRT2],
];

export function astar(g: CostGrid, start: Cell, goal: Cell): number[] | null {
  const { width: w, height: h } = g.meta;
  const s = start.row * w + start.col;
  const t = goal.row * w + goal.col;
  if (!Number.isFinite(g.cost[s]) || !Number.isFinite(g.cost[t])) return null;
  const minCost = Math.min(...Object.values(g.meta.cost));
  const dist = new Float64Array(w * h).fill(Infinity);
  const prev = new Int32Array(w * h).fill(-1);
  const heap = new MinHeap();
  const hEst = (i: number) =>
    minCost * Math.hypot(Math.floor(i / w) - goal.row, (i % w) - goal.col);
  dist[s] = 0;
  heap.push(hEst(s), s);
  while (heap.size) {
    const i = heap.pop();
    if (i === t) break;
    const r = Math.floor(i / w);
    const c = i % w;
    for (const [dr, dc, len] of STEPS) {
      const nr = r + dr;
      const nc = c + dc;
      if (nr < 0 || nc < 0 || nr >= h || nc >= w) continue;
      const j = nr * w + nc;
      const step = ((g.cost[i] + g.cost[j]) / 2) * len;
      if (!Number.isFinite(step)) continue;
      const d = dist[i] + step;
      if (d < dist[j]) {
        dist[j] = d;
        prev[j] = i;
        heap.push(d + hEst(j), j);
      }
    }
  }
  if (!Number.isFinite(dist[t])) return null;
  const path = [t];
  while (path[path.length - 1] !== s) path.push(prev[path[path.length - 1]]);
  return path.reverse();
}

export function describeRoute(g: CostGrid, cells: number[], start: Cell, goal: Cell): PlannedRoute {
  const w = g.meta.width;
  const share: Record<string, number> = Object.fromEntries(g.meta.classes.map((c) => [c, 0]));
  let lengthM = 0;
  for (let k = 1; k < cells.length; k++) {
    const a = cells[k - 1];
    const b = cells[k];
    const seg = Math.hypot(Math.floor(a / w) - Math.floor(b / w), (a % w) - (b % w)) * g.meta.cell_m;
    lengthM += seg;
    share[g.meta.classes[g.cls[b]]] += seg;
  }
  for (const key of Object.keys(share)) share[key] = lengthM ? share[key] / lengthM : 0;
  const straightM = Math.hypot(start.row - goal.row, start.col - goal.col) * g.meta.cell_m;
  const points = cells.map((i): RoutePoint => {
    const [lon, lat] = cellLonLat(g, i);
    return [lon, lat, g.height[i], g.cls[i]];
  });
  return { cells, lengthM, straightM, share, points };
}
