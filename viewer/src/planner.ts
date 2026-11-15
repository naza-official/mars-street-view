import { Vector2 } from "three";
import { fetchFresh } from "./catalog";
import { CostGrid, GridMeta, astar, describeRoute, lonLatToCell, parseGrid } from "./pathfind";
import type { RoutePoint } from "./route";
import { MarsScene } from "./scene";

export function costGridAssets(assets: Record<string, string>): string[] {
  return Object.entries(assets).filter(([k]) => k.endsWith("_costgrid")).map(([, v]) => v);
}

export async function loadCostGrid(baseUrl: string, asset: string): Promise<CostGrid | null> {
  const metaRes = await fetchFresh(`${baseUrl}${asset}`);
  if (!metaRes.ok) return null;
  const meta = (await metaRes.json()) as GridMeta;
  const bin = await fetchFresh(`${baseUrl}${meta.bin}`);
  return bin.ok ? parseGrid(meta, await bin.arrayBuffer()) : null;
}

type Pick = RoutePoint;

export class RoutePlanner {
  private active = false;
  private picks: Pick[] = [];
  private readonly button: HTMLButtonElement;
  private readonly result: HTMLElement;

  constructor(private readonly mars: MarsScene, private readonly grid: CostGrid,
    panel: HTMLElement) {
    this.button = document.createElement("button");
    this.button.dataset.action = "plan";
    this.button.textContent = "Plan route";
    this.button.addEventListener("click", () => this.toggle());
    this.result = document.createElement("div");
    this.result.className = "plan-result";
    const row = document.createElement("div");
    row.className = "sites plan-row";
    row.append(this.button);
    panel.append(row, this.result);
    this.bindCanvas();
  }

  toggle(): void {
    this.active = !this.active;
    this.button.classList.toggle("on", this.active);
    this.button.textContent = this.active ? "Planning… (click to stop)" : "Plan route";
    if (this.active) {
      this.picks = [];
      this.mars.clearPlan();
      this.say("Click a start point on the classified zone.");
    }
  }

  planBetween(start: Pick, end: Pick): string {
    const a = lonLatToCell(this.grid, start[0], start[1]);
    const b = lonLatToCell(this.grid, end[0], end[1]);
    if (!a || !b) return "Both points must be inside the classified zone.";
    const t0 = performance.now();
    const cells = astar(this.grid, a, b);
    const ms = performance.now() - t0;
    if (!cells) {
      this.mars.showPlan([], [start, end]);
      return "No route: a point is blocked (slope > 25° or no data) or the two are not connected.";
    }
    const r = describeRoute(this.grid, cells, a, b);
    this.mars.showPlan(r.points, [start, end]);
    const share = Object.entries(r.share).filter(([, v]) => v > 0.005)
      .map(([k, v]) => `${k.replace("_", " ")} ${(v * 100).toFixed(0)}%`).join(", ");
    return `Route ${(r.lengthM / 1000).toFixed(2)} km (straight ${(r.straightM / 1000).toFixed(2)} km, `
      + `+${((r.lengthM / Math.max(r.straightM, 1) - 1) * 100).toFixed(0)}%) · ${share} · `
      + `A* ${ms.toFixed(0)} ms`;
  }

  private say(text: string): void {
    this.result.textContent = text;
  }

  private onPick(ndc: Vector2): void {
    const c = this.mars.pick(ndc);
    if (!c) return;
    const cell = lonLatToCell(this.grid, c.lon, c.lat);
    if (!cell) return this.say("Outside the classified zone. Click inside the coloured square.");
    const pick: Pick = [c.lon, c.lat, c.height, 0];
    this.picks = this.picks.length >= 2 ? [pick] : [...this.picks, pick];
    if (this.picks.length === 1) {
      this.mars.showPlan([], this.picks);
      this.say("Start set. Click an end point.");
      return;
    }
    this.say(this.planBetween(this.picks[0], this.picks[1]) + " · click again for a new start");
  }

  private bindCanvas(): void {
    const canvas = this.mars.renderer.domElement;
    let down: { x: number; y: number } | null = null;
    canvas.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
    canvas.addEventListener("pointerup", (e) => {
      if (!this.active || this.mars.mode || !down) return;
      if (Math.abs(e.clientX - down.x) + Math.abs(e.clientY - down.y) > 4) return;
      const r = canvas.getBoundingClientRect();
      this.onPick(new Vector2(((e.clientX - r.left) / r.width) * 2 - 1,
        -((e.clientY - r.top) / r.height) * 2 + 1));
    });
  }
}
