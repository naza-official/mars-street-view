# D-002 Terrain tiler for a Mars sphere

## Context
`docs/contracts.md` says elevation is served as "terrain meshes in 3D Tiles 1.1 (GLB)", and Person B's rover terrain is
3D Tiles child tilesets. Most terrain tilers assume WGS84. M1 spike: try 2 to 3 approaches on a 512x512 MOLA crop
around Jezero, measure height correctness, tile count and size, and whether 3DTilesRendererJS loads them.

Spike code: `spikes/terrain_tiler/`. Input: `raw/spike/mola_jezero_512.tif` (built by
`python scripts/make_fixtures.py --out raw/spike --size 512 --only mola`): 512x512 px, 463 m, 4°x4° centred on
77.5E 18.4N, heights above the 3,396,190 m sphere, -5716 .. -738 m. Run 2026-11-14 on macOS, Python 3.11,
Node 22.15, 3d-tiles-renderer 0.5.4, three 0.180.0, 3d-tiles-validator 0.6.1.

Renderer check (`spikes/terrain_tiler/js/load_test.mjs`): 3DTilesRendererJS `TilesRenderer` in Node with a small
`window`/`requestAnimationFrame` shim, `tiles.ellipsoid = new Ellipsoid(3396190, 3396190, 3396190)`, camera over
Jezero; for each loaded model, every vertex goes through `matrixWorld` and `|p| - R` is compared with the DEM range.

## Options tried

### A. Custom grid mesher -> 3D Tiles 1.1 GLB (`approach_a_gltf.py`)
Quadtree, 65x65 vertices per tile, positions in Mars-fixed XYZ minus a tile-local origin (float32), origin carried in
`tile.transform` (child transform = child origin - parent origin), `box` bounding volumes (no `region`, which assumes
WGS84 in the spec), geometric error = measured max vertical error of the decimated grid.

| Metric | Result |
|---|---|
| Tiles | 85 (levels 0-3), 130-135 KB each |
| Size | 11.41 MB raw, 7.20 MB gzip |
| Stored height error (decode GLB, add origins, `|p|-R` vs DEM) | **0.003 m** max |
| Same, if absolute float32 XYZ were stored instead | 0.142 m (why the local origin matters) |
| Geometric error by level | 547, 309, 133, 0 m |
| 3d-tiles-validator | **0 errors, 0 warnings**, 85 infos (TEXCOORD_0 unused until a texture is attached) |
| 3DTilesRendererJS @ 300 km | 1 model, world heights -5617 .. -976 m |
| 3DTilesRendererJS @ 20 km | 17 models, depth 2, world heights -5684 .. -759 m, 0 errors |

### B. py3dtiles 12.1.1 (`approach_b_py3dtiles.py`)
Only takes point clouds (.las/.xyz/.ply) and EPSG codes for `--srs_in/--srs_out`, so Mars-fixed XYZ was precomputed
and passed without an SRS.

| Metric | Result |
|---|---|
| Tiles | 35 point-cloud GLBs, 4.01 MB raw, 2.55 MB gzip |
| Output | `POINTS` primitives, not a surface |
| Up axis | positions are z-up; read as z-up the heights are right (-5716 .. -738 m) |
| 3DTilesRendererJS | applies the standard glTF y-up rotation -> world heights **-314,684 .. +122,980 m** (wrong placement) |
| 3d-tiles-validator | **35 errors** (COLOR_0 as VEC3 UNSIGNED_BYTE, attribute data not 4-byte aligned) |

### C. quantized-mesh with a Mars ellipsoid (`approach_c_quantized_mesh.py`)
`quantized-mesh-encoder` 0.5.0 with `Ellipsoid(3396190, 3396190)`, `pydelatin` 0.4.0 TIN at 5 m max error, geographic
TMS levels 0-9, loaded with 3DTilesRendererJS `QuantizedMeshPlugin` (it takes the body from `tiles.ellipsoid`).

| Metric | Result |
|---|---|
| Tiles | 161 (levels 0-9); levels 6-9: 151 tiles, 3.21 MB raw, 2.54 MB gzip |
| Decoded height error vs DEM | 0.41 m (z9) .. 2.1 m (z6), from 16-bit u/v quantisation on crater walls |
| Header centre | on the Mars sphere (not WGS84) |
| 3DTilesRendererJS @ 20 km | 61 models, depth 10, 0 errors, all depths -5587 .. -973 m **after the fix below** |
| 3d-tiles-validator | not applicable (not 3D Tiles) |

Problems found in C:
1. The plugin needs `available` ranges in the terrain `layer.json`, otherwise it never refines past level 0.
2. With Delatin on large parent tiles (z1-z5), triangles span tens of degrees and their chords cut deep into the
   planet: world heights down to **-734 km**. Fixed by meshing parents on a regular 65x65 grid. Delatin's error only
   measures heights in the lon/lat grid, not the sphere's curvature. **Any mesher (A or C) must cap triangle size on
   coarse tiles.**
3. Quantized-mesh needs a file named `layer.json` at the terrain root, which collides with our contract `layer.json`
   in `vN/`. It would have to live in a subfolder (`vN/terrain/layer.json`).
4. Not the 3D Tiles format the contract names, so the viewer would need two terrain paths (quantized-mesh for L1/L2,
   3D Tiles for Person B).
5. Quantized-mesh heights are relative to the ellipsoid passed to the client, which is fine on a sphere, but the
   format still declares `"projection": "EPSG:4326"`, which is wrong for Mars.

## Size outlook for L1 (MOLA global, 46080 x 23040 px)
The crop is 1/4050 of the global grid. Scaling linearly (Jezero is rougher than average, so this is an upper bound):
- A as built (full-res leaves, float32, no simplification): ~46 GB raw, ~29 GB gzip. Too big.
- C at 5 m TIN error: ~13 GB raw, ~10 GB gzip.
- A plus TIN simplification (C shows ~3-5x fewer vertices at 5 m) plus `KHR_mesh_quantization` / `EXT_meshopt_compression`:
  expected low single-digit GB. **Not measured.** The `gltfpack` install was blocked by the agent permission
  policy in this session; measure it in M2 once the image is built.

## Recommendation
**Option A: our own mesher in `common/` writing 3D Tiles 1.1 GLB**, with these additions before M4:
1. TIN simplification per tile (pydelatin), with a maximum triangle edge on coarse levels so chords stay within
   the tile's geometric error (sagitta = R(1 - cos(θ/2))).
2. Shared tile edges or skirts so neighbouring LODs don't crack.
3. `KHR_mesh_quantization` + `EXT_meshopt_compression` (3DTilesRendererJS 0.5.4 supports meshopt via
   `GLTFExtensionsPlugin({ meshoptDecoder })`).
4. Explicit `box` bounding volumes only. Never `region`, which the spec defines on WGS84.

Why: correct heights to millimetres, the only option that passes the official validator, the format the contract
already names, and one renderer path for L1, L2 and Person B's rover tilesets. No dependency on a WGS84-only tool.
C is the fallback for L1 only, if A's sizes stay too large after compression.
Reject B: points not terrain, wrong up-axis in a standard renderer, invalid output.

## Open
- meshopt size measurement (M2).
- Visual check in a browser with real WebGL (M5); the spike loads geometry in Node only.

## Decision
_(human)_

## Date
2026-11-14 (opened)
