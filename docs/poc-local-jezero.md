# Local PoC: L1 + rover layers (PR #11) + viewer around the OEB landing site

Local only: branch `poc/local-jezero` (not pushed), data in `~/mars-data` (it doubles as a local copy of the bucket: `orbital/`, `rover/`, `catalog/`). No AWS credentials and no cloud cost. The PoC ran during the IAM Identity Center (us-east-1) 503 outage.

## Area
- Pilot stop `m2020_site1_drive52` (Octavia E. Butler landing site), L3 pose 18.444627 N, 77.450886 E, -4252.8 m above the sphere.
- 10x10 km rover box: lon 77.3620–77.5398 E, lat 18.3603–18.5290 N.
- L1 context: subtree root `6/25/27` = lon 75.9375–78.75 E, lat 16.875–19.6875 N (~167 km, whole crater), 5 tiles (levels 6–7, 463 m leaves).

## Phase 0: workspace (2026-11-15)
- `poc/local-jezero` from `m5-viewer`, merged `origin/b/rover-l3-l4-l6` (PR #11) locally. Conflicts: `ci.yml` install line → `.[dev,terrain,rover]`; `pyproject.toml` kept both A's extras (`dev` with moto, `terrain`, `infra`, `tiler`) and B's `rover`.
- `pip install -e ".[dev,terrain,rover]"`; `pytest -q` → `134 passed, 11 skipped`. `ruff check .` → 1 import-order issue in B's `layers/b_common/publish.py` (left for B).
- Docker: OrbStack (arm64) runs `ghcr.io/naza-official/mars-sv:v0.4.2` (amd64) under emulation with `--platform linux/amd64`; the repo is mounted over `/opt/msv`.

## Phase 1: L1 for the region
- New `layers/l1_global/region.py`: windowed read of MOLA/MDIM over `/vsicurl/` from the public `asc-pds-services` S3 HTTPS URL (no credentials), same PDS grid as the global ingest (edges on ±180/±90, GeoTIFF header origin ignored), then the same `dem_geoid` + PDS-areoid check, COGs. `planetarymaps.usgs.gov` URLs fail inside GDAL (302 redirect), the direct S3 HTTPS URL works.
- `scripts/poc/l1_layer.py` writes `layer.json` (`l1_global/jezero_poc/v1`, local-only site name).
```
$ docker run ... python -m layers.l1_global.region 6/25/27 /data/orbital/l1_global/jezero_poc/v1 --areoid ... --fetched 2026-11-15
{"extent": {"west": 75.9375, "south": 16.875, "east": 78.75, "north": 19.6875}, "dem_geoid_check": {"n": 2000, "mean_m": 0.027, "max_abs_m": 0.957}}   (54 s)
$ docker run ... python -m layers.l1_global.tiles .../dtm.tif .../v1 --ortho .../ortho.tif --root 6/25/27
"tiles": 5, "bytes": 1045300, levels 6 (1 tile, 7262 vertices) and 7 (4 tiles, 25773 vertices)
$ python -m common.validate ~/mars-data/orbital/l1_global/jezero_poc/v1
  asset dtm: height range: -5184.9 .. -1668.3 m
  asset ortho: size: 720x720x3 uint8 ... JPEG
ok
$ npx 3d-tiles-validator@0.6.1 --tilesetFile .../tileset.json
  "numErrors": 0, "numWarnings": 0
```

## Phase 2: rover layers (PR #11)
- L3: `layers/l3_poses/fetch.py` (PLACES + MMGIS) then `build.py` → `rover/l3_poses/jezero/v1/` (33,458 poses; MMGIS horizontal max 0.011 m, height p95 4.6 m — same as B's README).
- L4: `layers/l4_streetview/fetch.py --site-config configs/rover/jezero.yaml` (no `--frames`, ~4.3 GB). **Finding (B):** the default `--site-config` is `configs/sites/jezero.yaml`, which has no `pilot_stop` → `KeyError: 'pilot_stop'`.
- L4 build: `layers/l4_streetview/build.py` → `rover/l4_streetview/jezero/v1/` in 31 s: 1 stop, 8 NLFC wedges, 345,056 triangles, bbox [-136, -87, -0.3]..[163, 146, 6.2] m in stop ENU, 35 MB total. `common.validate`: ok. `3d-tiles-validator` on the stop tileset: 0 errors, 0 warnings (infos: NPOT 1280x960 textures). `alignment_rms_m` is null (pc_align pending, needs L2).
- `MSV_DATA=~/mars-data pytest -q tests/rover` → `25 passed, 3 skipped` (built-layer checks now run).
- L6: skipped (tables only, nothing to place in 3D).

## Phase 3: height check (L3 poses vs L1 DTM)
`scripts/poc/height_check.py` samples L1 `dtm.tif` at every L3 pose in the 10x10 km box.
```
pilot stop: pose -4252.8 m, L1 -4249.0 m, pose - L1 = -3.9 m
13,384 poses in box: median -9.7 m, p95 |46.4| m, min -51.3 m, max 35.3 m
```
Both are heights above the 3,396,190 m sphere. The spread is what 463 m MOLA smoothing gives; this is the baseline L2 (HiRISE DTM, M6) has to beat. The horizontal MDIM–MOLA offset (~0.9 km, D-008) also shows in the imagery.

## Phase 4: viewer on local data
- Local serving: `python3 -m http.server 8000 -d ~/mars-data` + `MSV_DATA_ORIGIN=http://127.0.0.1:8000 npx vite` (the Vite proxy makes data same-origin). `catalog/index.json` lists `l1_global/jezero_poc`, `l3_poses/jezero`, `l4_streetview/jezero`.
- Viewer changes (kept for M5/M7): every `tileset` / `*_tileset` asset is loaded (L4 has per-stop tilesets, no `tileset` key); L3 `poses_csv` is drawn as the traverse (always-on-top line, local origin to avoid float32 jitter); "OEB landing" site button; a dark base sphere 9 km below datum so areas without tiles are not empty.
- `npm test` → 19 passed; `npm run build` ok.
- Headless Chrome (playwright-core, installed Chrome, Metal ANGLE) check:
```
jezero {"cam":{"lat":18.4,"lon":77.5,"height":120000}, tiles visible 1}  readout: 18.4000°N  77.5000°E  -4298 m
oeb    {"cam":{"lat":18.4446,"lon":77.4509,"height":3000}, tiles loaded 5} readout: 18.4446°N  77.4509°E  -4247 m
```
Screenshots: `docs/poc/02_jezero.png` (crater with MDIM texture), `docs/poc/03_oeb.png` (traverse over L1).

- **Viewer bug found and fixed:** Jezero's floor is ~4.25 km *below* the 3,396,190 m datum, so every close-up camera sits inside the sphere `GlobeControls` uses. Its clip planes and fallback ray hit then go wrong (nothing rendered, readout on the far side of the sphere). Fix: the controls use a "floor" sphere 9 km below the datum (below Hellas); readout, fly-to and cartographic maths stay on the datum sphere. `placeCamera` takes azimuth/elevation.
- Stop close-ups (camera 245 m south of the stop, 60 m up, looking north 25° down):
```
06_mesh_only   (L1 hidden): tiles visible [L1 2, L4 1] — 8 wedges radiate ~150 m around the start of the traverse
07_mesh_on_l1  (L1 shown):  tiles visible [L1 2, L4 1] — mesh fully hidden under L1
```
Screenshots: `docs/poc/05_stop_oblique.png`, `docs/poc/06_mesh_only.png`, `docs/poc/07_mesh_on_l1.png`. Scripts: `scripts/poc/screens/shot.mjs`, `shot3.mjs` (playwright-core 1.64.1 + installed Chrome).

## Findings
1. **Rover content is buried in L1.** At the stop L1 is 3.9 m above the pose and the mesh rises only ~6 m; in the box L1 differs from poses by median -9.7 m, p95 46 m. L1 (463 m MOLA) cannot carry street-level content. Needs L2 (M6) and B's `pc_align` (`alignment_rms_m`). Until then the viewer could hide orbital terrain near a stop or draw rover tiles on top.
2. **Below-datum cameras** break GlobeControls with the datum sphere → fixed with a floor sphere (above). Applies to the real viewer (M5), not only the PoC.
3. **L4 mesh colour** renders as flat olive/yellow, not Navcam greyscale/colour; likely a material/texture issue in `stop.glb` (unlit vs vertex colours). To check with B.
4. **L4 fetch default config** (`configs/sites/jezero.yaml`) has no `pilot_stop` → `KeyError`; works with `--site-config configs/rover/jezero.yaml`. For B.
5. **L4 `layer.json` has no `tileset` asset**, only `<stop>_tileset`; the viewer now loads both forms. Worth writing into the contract with #22.
6. **`planetarymaps.usgs.gov` URLs fail in GDAL `/vsicurl/`** (302); the direct `asc-pds-services` S3 HTTPS URL works anonymously. Useful for M6 (no credentials needed for USGS reads).
7. **MDIM at close range** is very dark and blurry (232 m); expected, L2 HiRISE replaces it.

## How to run it again
```
python3 -m http.server 8000 --bind 127.0.0.1 -d ~/mars-data          # data
cd viewer && MSV_DATA_ORIGIN=http://127.0.0.1:8000 npx vite --port 5173   # viewer
open http://localhost:5173/viewer/        # Jezero / OEB landing buttons
```

## Next
- Feed findings 1, 3, 4, 5 to Person B (#11, #22).
- M6: reuse `layers/l1_global/region.py`'s windowed `/vsicurl/` read for the HiRISE/CTX clips; L2 is what makes the stop sit on terrain.
- M5: the floor-sphere fix and multi-tileset loading go into the viewer PR.
