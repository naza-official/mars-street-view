import {
  Matrix4, PerspectiveCamera, Raycaster, Scene, Vector2, Vector3, WebGLRenderer,
} from "three";
import { CAMERA_FRAME, Ellipsoid, GlobeControls, TilesRenderer } from "3d-tiles-renderer";
import { Cartographic, MARS_RADIUS_M, toDeg, toRad } from "./geo";
import { flightDuration, flightPoint } from "./flight";

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
  readonly controls: GlobeControls;
  readonly tilesets: TilesRenderer[] = [];
  private last = performance.now();
  private readonly raycaster = new Raycaster();
  private flight: Flight | null = null;
  private readonly frame = new Matrix4();

  constructor(canvasParent: HTMLElement) {
    this.renderer = new WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    this.renderer.setClearColor(0x05060a);
    canvasParent.appendChild(this.renderer.domElement);
    this.controls = new GlobeControls(this.scene, this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.setEllipsoid(this.ellipsoid, this.scene);
    (this.raycaster as Raycaster & { firstHitOnly?: boolean }).firstHitOnly = true;
    this.placeCamera({ lat: 18.4, lon: 77.5, height: 3 * MARS_RADIUS_M });
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  addTileset(url: string): TilesRenderer {
    const tiles = new TilesRenderer(url);
    tiles.ellipsoid.copy(this.ellipsoid);
    tiles.errorTarget = 12;
    tiles.setCamera(this.camera);
    tiles.setResolutionFromRenderer(this.camera, this.renderer);
    this.scene.add(tiles.group);
    this.tilesets.push(tiles);
    return tiles;
  }

  cameraCartographic(): Cartographic {
    const c = this.ellipsoid.getPositionToCartographic(this.camera.position, {});
    return { lat: toDeg(c.lat), lon: toDeg(c.lon), height: c.height };
  }

  placeCamera(c: Cartographic): void {
    this.ellipsoid.getObjectFrame(
      toRad(c.lat), toRad(c.lon), c.height, 0, -Math.PI / 2, 0, this.frame, CAMERA_FRAME,
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
    const hit = this.raycaster.intersectObjects(this.tilesets.map((t) => t.group), true)[0];
    const point = hit ? hit.point : this.ellipsoid.intersectRay(this.raycaster.ray, new Vector3());
    if (!point) return null;
    const c = this.ellipsoid.getPositionToCartographic(point, {});
    return { lat: toDeg(c.lat), lon: toDeg(c.lon), height: c.height };
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
    if (!this.flight) this.controls.update(dt);
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
