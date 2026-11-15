import { Material, Mesh, Object3D, Vector4 } from "three";

export type LonLatBox = [number, number, number, number];

export const MAX_CLIP_BOXES = 4;

export function clipUniform(boxes: LonLatBox[]): Vector4[] {
  const padded = [...boxes.slice(0, MAX_CLIP_BOXES)];
  while (padded.length < MAX_CLIP_BOXES) padded.push([0, 0, 0, 0]);
  return padded.map(([w, s, e, n]) => new Vector4(w, s, e, n));
}

const VERTEX_HEAD = "varying vec3 vMarsPos;\nvoid main() {";
const VERTEX_TAIL = "#include <project_vertex>\nvMarsPos = (modelMatrix * vec4(transformed, 1.0)).xyz;";
const FRAGMENT_HEAD = `varying vec3 vMarsPos;
uniform vec4 clipBoxes[${MAX_CLIP_BOXES}];
bool insideClip() {
  float lon = mod(degrees(atan(vMarsPos.y, vMarsPos.x)) + 360.0, 360.0);
  float lat = degrees(asin(vMarsPos.z / length(vMarsPos)));
  for (int i = 0; i < ${MAX_CLIP_BOXES}; i++) {
    vec4 b = clipBoxes[i];
    if (lon > b.x && lon < b.z && lat > b.y && lat < b.w) return true;
  }
  return false;
}
void main() {
  if (insideClip()) discard;`;

export function clipMaterial(material: Material, boxes: Vector4[]): void {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.clipBoxes = { value: boxes };
    shader.vertexShader = shader.vertexShader
      .replace("void main() {", VERTEX_HEAD)
      .replace("#include <project_vertex>", VERTEX_TAIL);
    shader.fragmentShader = shader.fragmentShader.replace("void main() {", FRAGMENT_HEAD);
  };
  material.needsUpdate = true;
}

export function clipObject(root: Object3D, boxes: Vector4[]): void {
  root.traverse((obj) => {
    const mesh = obj as Mesh;
    if (!mesh.isMesh) return;
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of materials) clipMaterial(m, boxes);
  });
}
