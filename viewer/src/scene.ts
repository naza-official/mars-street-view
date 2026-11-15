import {
  BufferGeometry, DoubleSide, Float32BufferAttribute, Group, Line, LineBasicMaterial, Mesh,
  CatmullRomCurve3, Color, InstancedMesh, MeshBasicMaterial, Object3D, PlaneGeometry, Shape,
  Material, NearestFilter, SRGBColorSpace, ShapeGeometry, TextureLoader, TubeGeometry,
  SphereGeometry, Matrix4, PerspectiveCamera, Raycaster, Scene, Vector2, Vector3, WebGLRenderer,
} from "three";
import { CAMERA_FRAME, Ellipsoid, GlobeControls, TilesRenderer } from "3d-tiles-renderer";
import { Cartographic, MARS_RADIUS_M, toDeg, toRad } from "./geo";
import { flightDuration, flightPoint } from "./flight";
import { TraversePoint } from "./traverse";
import { LonLatBox, clipObject, clipUniform } from "./clip";
import { PanoInfo, loadPanoMesh } from "./pano";
import { ClassMapData, RouteData, RoutePoint, classColor, gridIndex } from "./route";
import {
  ARROW_RANGE_M, EYE_HEIGHT_M, MoveInput, StopPoint, localOffset, nearbyStops, step,
} from "./walk";

export type FirstPersonMode = "pano" | "walk";

export interface FirstPerson {
  mode: FirstPersonMode;
  at: Cartographic;
  az: number;
  el: number;
  mesh?: Mesh;
  arrows?: Group;
  stopId?: string;
}

const GROUND_PROBE_M = 500;
const ROUTE_LIFT_M = 0.6;
const CELL_LIFT_M = 1.5;

function enuPoint(origin: Cartographic, p: RoutePoint, lift: number): Vector3 {
  const { north, east } = localOffset(origin, { lat: p[1], lon: p[0], height: p[2] });
  return new Vector3(east, north, p[2] - origin.height + lift);
}

function classMapGeometry(data: ClassMapData, origin: Cartographic): BufferGeometry {
  const pos: number[] = [];
  const uv: number[] = [];
  const v = data.vertices;
  for (let r = 0; r < data.rows; r++) {
    for (let c = 0; c < data.cols; c++) {
      const k = (r * data.cols + c) * 3;
      pos.push(...enuPoint(origin, [v[k], v[k + 1], v[k + 2], 0], CELL_LIFT_M).toArray());
      uv.push(data.u[c], data.v[r]);
    }
  }
  const g = new BufferGeometry();
  g.setAttribute("position", new Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new Float32BufferAttribute(uv, 2));
  g.setIndex(gridIndex(data.cols, data.rows));
  return g;
}

function routeCells(data: RouteData, origin: Cartographic): InstancedMesh {
  const plane = new PlaneGeometry(data.cell_m, data.cell_m);
  const material = new MeshBasicMaterial({
    transparent: true, opacity: 0.42, depthWrite: false, depthTest: false, side: DoubleSide,
  });
  const mesh = new InstancedMesh(plane, material, data.cells.length);
  mesh.renderOrder = 9;
  const m = new Matrix4();
  const colour = new Color();
  data.cells.forEach((p, i) => {
    m.makeTranslation(enuPoint(origin, p, CELL_LIFT_M));
    mesh.setMatrixAt(i, m);
    mesh.setColorAt(i, colour.setRGB(...classColor(data.legend, p[3])));
  });
  mesh.frustumCulled = false;
  return mesh;
}

function routeTube(data: RouteData, origin: Cartographic): Mesh {
  const pts = data.route.map((p) => enuPoint(origin, p, ROUTE_LIFT_M + 0.8));
  const path = new CatmullRomCurve3(pts, false, "catmullrom", 0.1);
  const geometry = new TubeGeometry(path, Math.max(64, pts.length * 4), 1.2, 6, false);
  const mesh = new Mesh(geometry, new MeshBasicMaterial({ color: 0xffffff }));
  mesh.frustumCulled = false;
  mesh.renderOrder = 12;
  mesh.userData.kind = "route";
  return mesh;
}

function arrowMesh(azDeg: number, dist: number, stopId: string): Mesh {
  const shape = new Shape();
  shape.moveTo(0, 0.35);
  shape.lineTo(0.3, -0.15);
  shape.lineTo(0, -0.03);
  shape.lineTo(-0.3, -0.15);
  shape.closePath();
  const material = new MeshBasicMaterial({
    color: 0xffffff, transparent: true, opacity: 0.75, depthTest: false, side: DoubleSide,
  });
  const mesh = new Mesh(new ShapeGeometry(shape), material);
  const az = toRad(azDeg);
  const r = Math.min(6, 3 + dist / 100);
  mesh.position.set(Math.sin(az) * r, Math.cos(az) * r, -EYE_HEIGHT_M);
  mesh.rotation.z = -az;
  mesh.renderOrder = 30;
  mesh.userData.stopId = stopId;
  return mesh;
}

const BASE_SPHERE_DEPTH_M = 9_000;
const FLOOR_RADIUS_M = MARS_RADIUS_M - BASE_SPHERE_DEPTH_M;
const TRAVERSE_LIFT_M = 3;
const SPACE_COLOR = 0x05060a;
const MARS_SKY_COLOR = 0xa8876b;

export interface Flight {
  from: Cartographic;
  to: Cartographic;
  start: number;
  duration: number;
}

export class MarsScene {
  readonly scene = new Scene();
  readonly camera = new PerspectiveCamera(50, 1, 1, 4 * MARS_RADIUS_M);
  readonly renderer: WebGLRenderer;
  readonly ellipsoid = new Ellipsoid(MARS_RADIUS_M, MARS_RADIUS_M, MARS_RADIUS_M);
  readonly floor = new Ellipsoid(FLOOR_RADIUS_M, FLOOR_RADIUS_M, FLOOR_RADIUS_M);
  readonly controls: GlobeControls;
  readonly tilesets: TilesRenderer[] = [];
  private last = performance.now();
  private readonly raycaster = new Raycaster();
  private flight: Flight | null = null;
  private fp: FirstPerson | null = null;
  private terrain: TilesRenderer[] = [];
  private readonly overlays: Object3D[] = [];
  private readonly routeGroups: Group[] = [];
  private classesWanted = true;
  private stops: StopPoint[] = [];
  private move: MoveInput = { forward: 0, right: 0, run: false };
  private readonly frame = new Matrix4();

  constructor(canvasParent: HTMLElement) {
    this.renderer = new WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    this.renderer.setClearColor(SPACE_COLOR);
    canvasParent.appendChild(this.renderer.domElement);
    this.controls = new GlobeControls(this.scene, this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.setEllipsoid(this.floor, this.scene);
    (this.raycaster as Raycaster & { firstHitOnly?: boolean }).firstHitOnly = true;
    this.scene.add(this.baseSphere());
    this.placeCamera({ lat: 18.4, lon: 77.5, height: 150_000 });
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  addTileset(url: string, clipBoxes: LonLatBox[] = []): TilesRenderer {
    const tiles = new TilesRenderer(url);
    if (clipBoxes.length) {
      const boxes = clipUniform(clipBoxes);
      tiles.addEventListener("load-model", ({ scene }) => clipObject(scene, boxes));
    }
    tiles.ellipsoid.copy(this.ellipsoid);
    tiles.errorTarget = 12;
    tiles.setCamera(this.camera);
    tiles.setResolutionFromRenderer(this.camera, this.renderer);
    this.scene.add(tiles.group);
    this.tilesets.push(tiles);
    return tiles;
  }

  addTraverse(points: TraversePoint[]): Line {
    const xyz = points.map((p) => this.ellipsoid.getCartographicToPosition(
      toRad(p.lat), toRad(p.lon), p.height + TRAVERSE_LIFT_M, new Vector3()));
    const origin = xyz[0].clone();
    const flat = xyz.flatMap((v) => v.sub(origin).toArray());
    const geometry = new BufferGeometry();
    geometry.setAttribute("position", new Float32BufferAttribute(flat, 3));
    const line = new Line(geometry, new LineBasicMaterial({ color: 0x4fd1ff, depthTest: false }));
    line.position.copy(origin);
    line.frustumCulled = false;
    line.renderOrder = 10;
    this.scene.add(line);
    this.overlays.push(line);
    return line;
  }

  async addClassMap(data: ClassMapData, baseUrl: string): Promise<Group> {
    const texture = await new TextureLoader().loadAsync(`${baseUrl}${data.texture}`);
    texture.magFilter = NearestFilter;
    texture.colorSpace = SRGBColorSpace;
    const v = data.vertices;
    const origin = { lon: v[0], lat: v[1], height: v[2] };
    const group = new Group();
    group.matrixAutoUpdate = false;
    group.matrix.copy(this.enuFrame(origin));
    const mesh = new Mesh(classMapGeometry(data, origin), new MeshBasicMaterial({
      map: texture, transparent: true, opacity: 0.5, depthTest: false, depthWrite: false,
      side: DoubleSide,
    }));
    mesh.renderOrder = 9;
    mesh.frustumCulled = false;
    mesh.userData.kind = "classes";
    group.add(mesh);
    this.scene.add(group);
    this.routeGroups.push(group);
    this.applyClassVisibility();
    return group;
  }

  addRoute(data: RouteData, withCells = true): Group {
    const first = data.route[0] ?? data.cells[0];
    const origin = { lat: first[1], lon: first[0], height: first[2] };
    const group = new Group();
    group.matrixAutoUpdate = false;
    group.matrix.copy(this.enuFrame(origin));
    group.add(routeTube(data, origin));
    if (withCells) {
      const cells = routeCells(data, origin);
      cells.userData.kind = "classes";
      group.add(cells);
    }
    this.scene.add(group);
    this.routeGroups.push(group);
    return group;
  }

  setClassesVisible(visible: boolean): void {
    this.classesWanted = visible;
    this.applyClassVisibility();
  }

  private applyClassVisibility(): void {
    const show = this.classesWanted && this.fp === null;
    for (const g of this.routeGroups) {
      for (const c of g.children) {
        if (c.userData.kind === "classes") c.visible = show;
        if (c.userData.kind === "route") ((c as Mesh).material as Material).depthTest = this.fp !== null;
      }
    }
  }

  setRoutesVisible(visible: boolean): void {
    for (const g of this.routeGroups) g.visible = visible;
    this.applyClassVisibility();
  }

  private baseSphere(): Mesh {
    const geometry = new SphereGeometry(FLOOR_RADIUS_M, 96, 48);
    geometry.rotateX(Math.PI / 2);
    return new Mesh(geometry, new MeshBasicMaterial({ color: 0x5a3a2a }));
  }

  cameraCartographic(): Cartographic {
    const c = this.ellipsoid.getPositionToCartographic(this.camera.position, {});
    return { lat: toDeg(c.lat), lon: toDeg(c.lon), height: c.height };
  }

  placeCamera(c: Cartographic, azimuthDeg = 0, elevationDeg = -90): void {
    this.ellipsoid.getObjectFrame(
      toRad(c.lat), toRad(c.lon), c.height, toRad(azimuthDeg), toRad(elevationDeg), 0,
      this.frame, CAMERA_FRAME,
    );
    this.frame.decompose(this.camera.position, this.camera.quaternion, this.camera.scale);
    this.camera.updateMatrixWorld();
  }

  flyTo(to: Cartographic): Flight {
    const from = this.cameraCartographic();
    this.flight = { from, to, start: performance.now(), duration: flightDuration(from, to) };
    this.controls.enabled = false;
    return this.flight;
  }

  pick(ndc: Vector2): Cartographic | null {
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.raycaster.intersectObjects(this.tilesets.map((t) => t.group), true)
      .find((h) => !(h.object instanceof Line));
    const point = hit ? hit.point : this.ellipsoid.intersectRay(this.raycaster.ray, new Vector3());
    if (!point) return null;
    const c = this.ellipsoid.getPositionToCartographic(point, {});
    return { lat: toDeg(c.lat), lon: toDeg(c.lon), height: c.height };
  }

  get mode(): FirstPersonMode | null {
    return this.fp?.mode ?? null;
  }

  get inPano(): boolean {
    return this.fp?.mode === "pano";
  }

  get firstPerson(): FirstPerson | null {
    return this.fp;
  }

  setTerrain(finestFirst: TilesRenderer[]): void {
    this.terrain = finestFirst;
    for (const t of this.tilesets) if (!finestFirst.includes(t)) this.overlays.push(t.group);
  }

  setStops(stops: StopPoint[]): void {
    this.stops = stops;
  }

  async enterPano(info: PanoInfo, baseUrl: string, stopId: string): Promise<void> {
    const c = info.camera;
    const at = { lat: c.lat_n, lon: c.lon_e, height: c.height_above_sphere_m };
    const mesh = await loadPanoMesh(info, baseUrl, this.enuFrame(at));
    const view = this.fp ?? { az: 0, el: 0 };
    this.leaveFirstPerson();
    this.scene.add(mesh);
    const arrows = this.stopArrows(at, stopId);
    this.scene.add(arrows);
    this.fp = { mode: "pano", at, az: view.az, el: view.el, mesh, arrows, stopId };
    this.setRoutesVisible(false);
    this.beginFirstPerson(MARS_SKY_COLOR, 70);
  }

  enterWalk(at: Cartographic): void {
    const view = this.fp ?? { az: 0, el: -5 };
    this.leaveFirstPerson();
    this.fp = { mode: "walk", at: { ...at }, az: view.az, el: view.el };
    this.setRoutesVisible(true);
    this.beginFirstPerson(MARS_SKY_COLOR, 70);
  }

  exitFirstPerson(): void {
    if (!this.fp) return;
    const at = this.fp.at;
    this.leaveFirstPerson();
    this.renderer.setClearColor(SPACE_COLOR);
    this.camera.fov = 50;
    this.camera.updateProjectionMatrix();
    this.controls.enabled = true;
    for (const o of this.overlays) o.visible = true;
    this.setRoutesVisible(true);
    this.placeCamera({ ...at, height: at.height + 400 }, 0, -60);
  }

  look(dAz: number, dEl: number): void {
    if (!this.fp) return;
    this.fp.az = (this.fp.az + dAz + 360) % 360;
    this.fp.el = Math.max(-85, Math.min(85, this.fp.el + dEl));
  }

  zoom(dFov: number): void {
    this.camera.fov = Math.max(15, Math.min(100, this.camera.fov + dFov));
  }

  setMove(input: MoveInput): void {
    this.move = input;
  }

  pickArrow(ndc: Vector2): string | null {
    if (!this.fp?.arrows) return null;
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.raycaster.intersectObjects(this.fp.arrows.children, false)[0];
    return (hit?.object.userData.stopId as string | undefined) ?? null;
  }

  groundHeight(at: Cartographic): number | null {
    const up = this.ellipsoid.getCartographicToNormal(toRad(at.lat), toRad(at.lon), new Vector3());
    const origin = this.ellipsoid.getCartographicToPosition(
      toRad(at.lat), toRad(at.lon), at.height + GROUND_PROBE_M, new Vector3());
    this.raycaster.set(origin, up.negate());
    for (const tiles of this.terrain) {
      const hit = this.raycaster.intersectObject(tiles.group, true)[0];
      if (hit) return this.ellipsoid.getPositionToCartographic(hit.point, {}).height;
    }
    return null;
  }

  private enuFrame(at: Cartographic): Matrix4 {
    return this.ellipsoid.getEastNorthUpFrame(toRad(at.lat), toRad(at.lon), at.height, new Matrix4());
  }

  private stopArrows(at: Cartographic, stopId: string): Group {
    const group = new Group();
    group.matrixAutoUpdate = false;
    group.matrix.copy(this.enuFrame(at));
    for (const n of nearbyStops(at, this.stops, ARROW_RANGE_M, stopId)) {
      group.add(arrowMesh(n.az, n.dist, n.stop.id));
    }
    return group;
  }

  private beginFirstPerson(clear: number, fov: number): void {
    this.flight = null;
    this.controls.enabled = false;
    this.renderer.setClearColor(clear);
    this.camera.fov = fov;
    for (const o of this.overlays) o.visible = false;
  }

  private leaveFirstPerson(): void {
    if (!this.fp) return;
    for (const obj of [this.fp.mesh, this.fp.arrows]) if (obj) this.scene.remove(obj);
    this.fp.mesh?.geometry.dispose();
    this.fp = null;
  }

  private stepFirstPerson(dt: number): void {
    if (!this.fp) return;
    if (this.fp.mode === "walk") this.stepWalk(dt);
    this.placeCamera(this.fp.at, this.fp.az, this.fp.el);
    this.camera.near = 0.05;
    this.camera.far = 2_000_000;
    this.camera.updateProjectionMatrix();
  }

  private stepWalk(dt: number): void {
    if (!this.fp) return;
    const moving = this.move.forward !== 0 || this.move.right !== 0;
    const next = moving ? step(this.fp.at, this.fp.az, this.move, Math.min(dt, 0.1)) : this.fp.at;
    const ground = this.groundHeight(next);
    if (ground !== null) next.height = ground + EYE_HEIGHT_M;
    this.fp.at = next;
  }

  start(): void {
    this.renderer.setAnimationLoop(() => this.tick());
  }

  private stepFlight(now: number): void {
    if (!this.flight) return;
    const t = (now - this.flight.start) / (this.flight.duration * 1000);
    this.placeCamera(flightPoint(this.flight.from, this.flight.to, t));
    if (t >= 1) {
      this.flight = null;
      this.controls.enabled = true;
    }
  }

  private tick(): void {
    const now = performance.now();
    const dt = (now - this.last) / 1000;
    this.last = now;
    this.stepFlight(now);
    if (this.fp) this.stepFirstPerson(dt);
    else if (!this.flight) this.controls.update(dt);
    this.camera.updateMatrixWorld();
    for (const tiles of this.tilesets) tiles.update();
    this.renderer.render(this.scene, this.camera);
  }

  private resize(): void {
    const { innerWidth: w, innerHeight: h } = window;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    for (const tiles of this.tilesets) tiles.setResolutionFromRenderer(this.camera, this.renderer);
  }
}
