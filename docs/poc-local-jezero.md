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

## Phase 5: L2 Jezero + street view (2026-11-15)
**Why:** with only L1 the stop mesh was buried (finding 1), and the page opened 10,000 km out, so all you saw was the brown base sphere.

- **Sources** (public, anonymous HTTPS from `asc-pds-services/mosaic/mars2020_trn/`): HiRISE `JEZ_hirise_soc_006_DTM_..._1m_..._blend40.tif` (1.84 GB) + `JEZ_hirise_soc_006_orthoMosaic_25cm_..._first.tif` (7.36 GB), CTX `JEZ_ctx_B_soc_008` 20 m DTM + 6 m ortho for fill. All stored as uncompressed 1-row strips without overviews, so a window still downloads whole rows; fetched whole with `scripts/poc/fetch_ranges.py` (12 parallel ranges, ~26 MB/s). The first attempt hung on a stalled range (no timeout); the script now has 30 s timeouts, retries and resume.
- **Datum:** the HiRISE DTM at OEB reads -2569.90 m = PLACES `elevation_above_areoid_m` -2569.91 → heights are above the areoid (despite "IAU sphere" in the metadata, which is the horizontal CRS). Converted with the PDS MEGDR areoid (`common.datum.areoid_offset`): offset at OEB -1683.19 m vs PLACES -1682.92 m (0.27 m).
- **Build:** `layers/l2_regional/poc.py` warps HiRISE (CTX where empty: 51 % of the tile) onto the tile grid of root `9/203/220` (lon 77.3438–77.6953, lat 18.2812–18.6328, ~20 km), 11520² DTM (1.8 m posts), 32768² tinted ortho (0.64 m). Tiler: `--leaf-level 15 --texture-px 512 --split-level 12 --leaf-error 0.5` (new options in `layers/l1_global/tiles.py`).
```
L2 dtm: 20 s; ortho: 85 s; tiles: 41.6 s → 5461 tiles, 236 MB (levels 9-15, 900,714 leaf vertices)
python -m common.validate .../l2_regional/jezero_poc/v1   → ok (after COG conversion)
3d-tiles-validator .../tileset.json                       → numErrors 0, numWarnings 0
```
- **Height check vs L2** (16,611 poses inside the tile):

| | L1 (MOLA 463 m) | L2 (HiRISE/CTX) |
|---|---|---|
| pilot stop, pose − terrain | −3.9 m | **+0.26 m** |
| poses, median | −9.7 m | **−0.26 m** |
| poses, p95 \|Δ\| | 46.4 m | **1.2 m** |

- **Viewer:**
  - Opens at Jezero (150 km), not from deep space.
  - L1 is cut out (fragment discard, lon/lat box) wherever an L2 layer exists, so the terrains don't fight.
  - **Street view:** a "Street view: <stop>" button per `<stop>_pano` asset. It puts the camera at `pano.json → camera` (Navcam eye), on an inside-out sphere (50 m) built in the stop's ENU frame from the equirect (col 0 = north, azimuth clockwise). Drag looks around, the wheel zooms the field of view, and Esc or the button exits. Above +19° (no imagery) shows a Mars-sky colour.
  - `npm test` 24 passed, `npm run build` ok.
- **Screenshots:**
  - `docs/poc/08_start.png`: start view, L2 patch on L1.
  - `09_l2_overview.png`: HiRISE delta + traverse.
  - `10_stop_on_l2.png`: stop mesh sitting on L2.
  - `11_street_north.png` / `12_street_south.png`: Navcam 360 at OEB.

### Remaining findings
- L4 mesh colour (finding 3, corrected): not a material bug. The GLB textures are the raw Navcam colour (checked by extracting a texture: yellow-green, not white-balanced); from a distance they average to olive. Needs white balance in B's build.
- L1 (MDIM) vs L2 (HiRISE) imagery is offset by ~0.9 km at the L2 edge (D-008), and the brightness/tint differ.
- The panorama has a vertical seam / colour step (the frame seams B noted).
- Street view is entered by button only; clicking the stop in 3D is next.

## Phase 6: more stops + walking (2026-11-15)
- **What a stop costs:** the panorama needs only the `RAS_..._CYL_S_*` product (38–150 MB + label). The 4.5 GB of the pilot stop is mostly other panorama variants (1.4 GB), ORR image + DEM for `pc_align` (0.8 GB) and Navcam meshes (2.3 GB).
- **Stops:** the mosaic inventory has 24 stops with a left-eye RAS CYL_S 360 (sols 2–409). All are inside the L2 tile. `scripts/poc/fetch_panos.py` downloads only those products through B's PDS helpers (~1.7 GB in total). L4 `build.py` gets a fallback `RAS_{code}_CYL_S_` pattern, because suffixes vary (`CLRREPR`, `MANUGEN`, `POSTDRV`, ...). This is a PoC change for B.
- `layers/l4_streetview/build.py` → 24 stops in 8.5 min, `common.validate` ok, layer size 669M.
- **Viewer walking (`src/walk.ts`, `src/street.ts`):**
  - Stop picker with "Street view (24 stops)" and "Walk here".
  - In a panorama: white ground arrows toward every panorama stop within 400 m. Click an arrow, or W/↑, to jump to the stop you're facing (±50°). F walks from here.
  - Walking: WASD/arrows at 2 m/s (Shift 12 m/s), drag to look, eye 1.8 m above ground (ray down, finest terrain first). E opens the panorama of a stop within 20 m. Esc returns to orbit.
  - Traverse line and rover meshes are hidden in first person (the decimated meshes look like debris at eye level).
- **Headless check (`scripts/poc/screens/shot5.mjs`):**
```
pano      stop m2020_site1_drive52, 9 arrows (site3 drive1392/792/2208, site4 drive0/510/922/1712, site3 drive2346, site10 drive1114)
after W   stop m2020_site3_drive1392
walk      start h -4251.39 (pano eye -4252.01); after 5 s of W: +9.9 m north
after E   back in pano m2020_site3_drive1392; after Esc: orbit
npm test  27 passed; npm run build ok
```
- **Bug fixed:** the first walk height was 6 m too high because the ground ray also hit L1, which is only hidden visually under L2. The probe now tries terrain finest-first.
- **Screenshots:** `docs/poc/13_pano_arrows.png`, `14_next_stop.png` (partial POSTDRV panorama with a baked-in azimuth grid; L2 terrain shows through the gap), `15_walk.png`.
- **Panorama quality varies by variant:** some (`POSTDRV`, `CLRREPR`) are partial or carry grid/tick overlays. A per-stop preference list (cleanest variant first) would help; for B.
