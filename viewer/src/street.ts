import { Vector2 } from "three";
import { ResolvedLayer } from "./catalog";
import { PanoInfo } from "./pano";
import { MarsScene } from "./scene";
import { ENTER_PANO_RADIUS_M, MoveInput, StopPoint, nearbyStops } from "./walk";

interface StopFeature {
  id: string;
  geometry: { coordinates: [number, number, number] };
}

const HINTS: Record<string, string> = {
  pano: "Drag to look · click an arrow or W/↑ to go to the next stop · F walk from here · Esc exit",
  walk: "WASD/arrows move · Shift run · drag to look · E enter street view at a stop · Esc exit",
};

const MOVE_KEYS: Record<string, [keyof MoveInput, number]> = {
  KeyW: ["forward", 1], ArrowUp: ["forward", 1], KeyS: ["forward", -1], ArrowDown: ["forward", -1],
  KeyD: ["right", 1], ArrowRight: ["right", 1], KeyA: ["right", -1], ArrowLeft: ["right", -1],
};

export async function loadStops(layers: ResolvedLayer[]): Promise<StopPoint[]> {
  const lists = await Promise.all(layers.filter((l) => l.layer.assets.stops).map(async (l) => {
    const res = await fetch(`${l.baseUrl}${l.layer.assets.stops}`);
    if (!res.ok) return [];
    const fc = (await res.json()) as { features: StopFeature[] };
    return fc.features.map((f) => toStop(f, l));
  }));
  return lists.flat();
}

function toStop(f: StopFeature, l: ResolvedLayer): StopPoint {
  const [lon, lat, height] = f.geometry.coordinates;
  const pano = l.layer.assets[`${f.id}_pano`];
  return { id: f.id, lon, lat, height, panoUrl: pano ? `${l.baseUrl}${pano}` : null };
}

export class StreetView {
  private stops: StopPoint[] = [];
  private readonly held = new Set<string>();
  private readonly hint: HTMLElement;

  constructor(private readonly mars: MarsScene) {
    this.hint = document.createElement("div");
    this.hint.className = "hint";
    document.body.appendChild(this.hint);
    this.bindPointer();
    this.bindKeys();
  }

  setStops(stops: StopPoint[]): void {
    this.stops = stops;
    this.mars.setStops(stops);
  }

  get panoStops(): StopPoint[] {
    return this.stops.filter((s) => s.panoUrl);
  }

  async open(stopId: string): Promise<void> {
    const stop = this.stops.find((s) => s.id === stopId);
    if (!stop?.panoUrl) return;
    const info = (await (await fetch(stop.panoUrl)).json()) as PanoInfo;
    const base = stop.panoUrl.slice(0, stop.panoUrl.lastIndexOf("/") + 1);
    await this.mars.enterPano(info, base, stop.id);
    this.refresh();
  }

  walk(at: { lat: number; lon: number; height: number }): void {
    this.mars.enterWalk(at);
    this.refresh();
  }

  exit(): void {
    this.mars.exitFirstPerson();
    this.refresh();
  }

  private refresh(): void {
    const mode = this.mars.mode;
    document.body.classList.toggle("street", mode !== null);
    this.hint.textContent = mode ? HINTS[mode] : "";
  }

  private ndc(e: PointerEvent): Vector2 {
    const r = this.mars.renderer.domElement.getBoundingClientRect();
    return new Vector2(((e.clientX - r.left) / r.width) * 2 - 1,
      -((e.clientY - r.top) / r.height) * 2 + 1);
  }

  private bindPointer(): void {
    const canvas = this.mars.renderer.domElement;
    let last: { x: number; y: number } | null = null;
    let travel = 0;
    canvas.addEventListener("pointerdown", (e) => {
      if (!this.mars.mode) return;
      last = { x: e.clientX, y: e.clientY };
      travel = 0;
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!last || !this.mars.mode) return;
      const k = this.mars.camera.fov / canvas.clientHeight;
      this.mars.look(-(e.clientX - last.x) * k, (e.clientY - last.y) * k);
      travel += Math.abs(e.clientX - last.x) + Math.abs(e.clientY - last.y);
      last = { x: e.clientX, y: e.clientY };
    });
    canvas.addEventListener("pointerup", (e) => {
      if (last && travel < 5 && this.mars.inPano) {
        const id = this.mars.pickArrow(this.ndc(e));
        if (id) void this.open(id);
      }
      last = null;
    });
    canvas.addEventListener("wheel", (e) => { if (this.mars.mode) this.mars.zoom(e.deltaY * 0.02); });
  }

  private bindKeys(): void {
    window.addEventListener("keydown", (e) => this.keyDown(e));
    window.addEventListener("keyup", (e) => {
      this.held.delete(e.code);
      this.updateMove(e.shiftKey);
    });
  }

  private keyDown(e: KeyboardEvent): void {
    if (!this.mars.mode) return;
    if (e.code === "Escape") return this.exit();
    if (this.mars.inPano) return this.panoKey(e.code);
    if (e.code === "KeyE") return void this.enterNearest();
    if (MOVE_KEYS[e.code]) e.preventDefault();
    this.held.add(e.code);
    this.updateMove(e.shiftKey);
  }

  private panoKey(code: string): void {
    const fp = this.mars.firstPerson;
    if (!fp) return;
    if (code === "KeyF") return this.walk(fp.at);
    if (code === "KeyW" || code === "ArrowUp") void this.ahead(fp.at, fp.az, fp.stopId);
  }

  private async ahead(at: StopPoint | { lat: number; lon: number; height: number }, az: number,
    skip?: string): Promise<void> {
    const off = (a: number) => Math.abs(((a - az + 540) % 360) - 180);
    const best = nearbyStops(at, this.stops, 400, skip).filter((s) => off(s.az) < 50)
      .sort((a, b) => off(a.az) - off(b.az))[0];
    if (best) await this.open(best.stop.id);
  }

  private async enterNearest(): Promise<void> {
    const fp = this.mars.firstPerson;
    if (!fp) return;
    const near = nearbyStops(fp.at, this.stops, ENTER_PANO_RADIUS_M)[0];
    if (near) await this.open(near.stop.id);
  }

  private updateMove(run: boolean): void {
    const input: MoveInput = { forward: 0, right: 0, run };
    for (const code of this.held) {
      const k = MOVE_KEYS[code];
      if (k && k[0] !== "run") (input[k[0]] as number) += k[1];
    }
    this.mars.setMove(input);
  }
}
