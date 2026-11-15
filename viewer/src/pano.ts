import {
  BufferGeometry, DoubleSide, Float32BufferAttribute, Matrix4, Mesh, MeshBasicMaterial,
  SRGBColorSpace, Texture, TextureLoader,
} from "three";

export const PANO_RADIUS_M = 50;

export interface PanoInfo {
  stop_id: string;
  azimuth_of_equirect_col0_deg: number;
  camera: { lon_e: number; lat_n: number; height_above_sphere_m: number };
  equirect: { file: string; width: number; height: number };
}

export interface PanoEntry {
  stopId: string;
  url: string;
}

export function panoEntries(baseUrl: string, assets: Record<string, string>): PanoEntry[] {
  return Object.entries(assets)
    .filter(([name]) => name.endsWith("_pano"))
    .map(([name, path]) => ({ stopId: name.slice(0, -"_pano".length), url: `${baseUrl}${path}` }));
}

export function enuDirection(azDeg: number, elDeg: number): [number, number, number] {
  const az = (azDeg * Math.PI) / 180;
  const el = (elDeg * Math.PI) / 180;
  return [Math.sin(az) * Math.cos(el), Math.cos(az) * Math.cos(el), Math.sin(el)];
}

export function panoGeometry(col0AzDeg: number, azSteps = 128, elSteps = 64): BufferGeometry {
  const positions: number[] = [];
  const uvs: number[] = [];
  for (let j = 0; j <= elSteps; j++) {
    const v = j / elSteps;
    for (let i = 0; i <= azSteps; i++) {
      const u = i / azSteps;
      const dir = enuDirection(col0AzDeg + u * 360, v * 180 - 90);
      positions.push(...dir.map((c) => c * PANO_RADIUS_M));
      uvs.push(u, v);
    }
  }
  const index: number[] = [];
  const row = azSteps + 1;
  for (let j = 0; j < elSteps; j++) {
    for (let i = 0; i < azSteps; i++) {
      const a = j * row + i;
      index.push(a, a + 1, a + row, a + 1, a + row + 1, a + row);
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new Float32BufferAttribute(uvs, 2));
  geometry.setIndex(index);
  return geometry;
}

export async function loadPanoMesh(info: PanoInfo, baseUrl: string, enu: Matrix4): Promise<Mesh> {
  const texture: Texture = await new TextureLoader().loadAsync(`${baseUrl}${info.equirect.file}`);
  texture.colorSpace = SRGBColorSpace;
  const material = new MeshBasicMaterial({
    map: texture, side: DoubleSide, transparent: true, depthTest: false, depthWrite: false,
  });
  const mesh = new Mesh(panoGeometry(info.azimuth_of_equirect_col0_deg), material);
  mesh.matrixAutoUpdate = false;
  mesh.matrix.copy(enu);
  mesh.renderOrder = 20;
  mesh.frustumCulled = false;
  return mesh;
}
