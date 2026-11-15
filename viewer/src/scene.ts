import {
  BufferGeometry, Float32BufferAttribute, Line, LineBasicMaterial, Mesh, MeshBasicMaterial,
  SphereGeometry, Matrix4, PerspectiveCamera, Raycaster, Scene, Vector2, Vector3, WebGLRenderer,
} from "three";
import { CAMERA_FRAME, Ellipsoid, GlobeControls, TilesRenderer } from "3d-tiles-renderer";
import { Cartographic, MARS_RADIUS_M, toDeg, toRad } from "./geo";
import { flightDuration, flightPoint } from "./flight";
import { TraversePoint } from "./traverse";
import { LonLatBox, clipObject, clipUniform } from "./clip";
import { PanoInfo, loadPanoMesh } from "./pano";

interface PanoState {
  mesh: Mesh;
  at: Cartographic;
  az: number;
  el: number;
}

const BASE_SPHERE_DEPTH_M = 9_000;
const FLOOR_RADIUS_M = MARS_RADIUS_M - BASE_SPHERE_DEPTH_M;
const TRAVERSE_LIFT_M = 3;

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
  private pano: PanoState | null = null;
  private readonly frame = new Matrix4();

  constructor(canvasParent: HTMLElement) {
    this.renderer = new WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    this.renderer.setClearColor(0x05060a);
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
    return line;
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

  get inPano(): boolean {
    return this.pano !== null;
  }

  async enterPano(info: PanoInfo, baseUrl: string): Promise<void> {
    this.exitPano(false);
    const c = info.camera;
    const at = { lat: c.lat_n, lon: c.lon_e, height: c.height_above_sphere_m };
    const enu = this.ellipsoid.getEastNorthUpFrame(
      toRad(at.lat), toRad(at.lon), at.height, new Matrix4());
    const mesh = await loadPanoMesh(info, baseUrl, enu);
    this.scene.add(mesh);
    this.flight = null;
    this.controls.enabled = false;
    this.pano = { mesh, at, az: 0, el: 0 };
    this.camera.fov = 70;
  }

  exitPano(toOrbit = true): void {
    if (!this.pano) return;
    const { mesh, at } = this.pano;
    this.scene.remove(mesh);
    mesh.geometry.dispose();
    this.pano = null;
    this.camera.fov = 50;
    this.camera.updateProjectionMatrix();
    this.controls.enabled = true;
    if (toOrbit) this.placeCamera({ ...at, height: at.height + 400 }, 0, -60);
  }

  lookPano(dAz: number, dEl: number): void {
    if (!this.pano) return;
    this.pano.az = (this.pano.az + dAz + 360) % 360;
    this.pano.el = Math.max(-85, Math.min(85, this.pano.el + dEl));
  }

  zoomPano(dFov: number): void {
    this.camera.fov = Math.max(15, Math.min(100, this.camera.fov + dFov));
  }

  private stepPano(): void {
    if (!this.pano) return;
    this.placeCamera(this.pano.at, this.pano.az, this.pano.el);
    this.camera.near = 0.05;
    this.camera.far = 2_000_000;
    this.camera.updateProjectionMatrix();
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
    if (this.pano) this.stepPano();
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
