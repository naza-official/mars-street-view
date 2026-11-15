import { describe, expect, it } from "vitest";
import { MeshBasicMaterial } from "three";
import { MAX_CLIP_BOXES, clipMaterial, clipUniform } from "../src/clip";

describe("clip", () => {
  it("pads clip boxes to a fixed uniform size", () => {
    const u = clipUniform([[77.3, 18.3, 77.7, 18.6]]);
    expect(u).toHaveLength(MAX_CLIP_BOXES);
    expect(u[0].toArray()).toEqual([77.3, 18.3, 77.7, 18.6]);
    expect(u[1].toArray()).toEqual([0, 0, 0, 0]);
  });

  it("injects the discard test into the fragment shader", () => {
    const m = new MeshBasicMaterial();
    clipMaterial(m, clipUniform([[1, 2, 3, 4]]));
    const shader = {
      uniforms: {} as Record<string, { value: unknown }>,
      vertexShader: "void main() {\n#include <project_vertex>\n}",
      fragmentShader: "void main() {\n}",
    };
    m.onBeforeCompile(shader as never, undefined as never);
    expect(shader.fragmentShader).toContain("if (insideClip()) discard;");
    expect(shader.vertexShader).toContain("vMarsPos = (modelMatrix");
    expect(shader.uniforms.clipBoxes.value).toHaveLength(MAX_CLIP_BOXES);
  });
});
