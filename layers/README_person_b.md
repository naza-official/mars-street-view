# Person B lane — rover data (L3, L4, L6 + MEDA for L5)

Owner: Person B. Everything the rovers measured. The orbital layers (L1, L2, L5 orbital) and
the viewer (L8) belong to Person A. The two lanes meet at the **stop record**
(see the build plan, "Stop record").

## Data location

Raw downloads go to `$MSV_DATA/raw/<layer>/` (default `~/mars-data`), mirroring the shared
bucket layout `raw/<layer>/`. Nothing under it goes into Git.

```bash
python3 -m venv ~/.venvs/msv && ~/.venvs/msv/bin/pip install -e ".[dev,rover]"   # common/ + rover deps
export MSV_DATA=~/mars-data            # optional
~/.venvs/msv/bin/python layers/l3_poses/fetch.py
~/.venvs/msv/bin/python layers/l4_streetview/fetch.py --frames     # pilot stop, ~8 GB
~/.venvs/msv/bin/python layers/l6_radiation_atmo/fetch.py --only meda
~/.venvs/msv/bin/python layers/l6_radiation_atmo/fetch.py --only rad --sol-step 10
```

Every fetch is resumable and skips files that are already complete.

## What each fetch pulls

| Layer | Source | Size | Notes |
| --- | --- | --- | --- |
| L3 | [PLACES bundle](https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_rover_places/) (localizations, orbital origins, mechanisms, SIS) | ~370 MB | 3.9 GB PLACES orbital map skipped (`--with-orbital-map`) |
| L3 | MMGIS `M20_waypoints.json`, `M20_traverse.json` | <1 MB | cross-check |
| L4 | Navcam mosaics for the stop: `CYL_S` RAS/RNG/XYZ panoramas, ORR 45 m DEM + image, all labels | 2.1 GB | `--all-mosaics` = 29.6 GB |
| L4 | Navcam meshes for the stop (.obj/.mtl/.png + labels) | 2.2 GB | |
| L4 | Calibrated full-frame Navcam RAS frames (`--frames`) | 3.8 GB | for our own stitcher / CAHVORE |
| L6/L5 | MEDA `meda_derived.tar.gz` (PS, RHS, TIRS, WS, ANCILLARY) | 3.7 GB | read in place, not extracted |
| L6 | MSL RAD RDR, dosimetry only, streamed → `rad/rad_dose.csv` | few MB | full archive ~150 GB is never stored |

## Pilot stop

`m2020_site1_drive52` — Octavia E. Butler landing site, sol 2 (configs/rover/jezero.yaml).
Picked because it has the most Navcam mosaic products of any stop (12 cylindrical-perspective,
12 cylindrical, 36 orthorectified) and 43 meshes, and it sits inside the Jezero HiRISE ortho.

## Product codes worth knowing (Navcam mosaics)

`N_LRGB_<sol>_<TYPE>_<site:3><drive:4>_<PROJ>_<L/R/S>_<variant>`

- TYPE: `RAS` radiometric image, `RNG` range, `XYZ` per-pixel XYZ (site frame), `DEM` height,
  `ICM`/`IDX` image coordinate/index maps, `RZS`, `UVW` normals, `SLP` slope.
- PROJ: `CYL` cylindrical (the 360 panorama), `CYP` cyl-perspective (stereo), `POL` polar,
  `VRT` vertical, `ORR` orthorectified.

## Build status (2026-11-14)

| Layer | Command | Output (`$MSV_DATA/rover/<layer>/<site>/v1/`, mirrors the bucket) | Status |
| --- | --- | --- | --- |
| L3 | `layers/l3_poses/build.py` | `l3_poses/jezero/v1/`: `poses.parquet` (GeoParquet), `poses.csv`, `crosscheck_mmgis.csv`, `report.json`, `layer.json` | 33,458 site/drive poses, 88 sites, sols 0–1897 |
| L4 | `layers/l4_streetview/build.py` | `l4_streetview/jezero/v1/`: `stops.geojson`, `<stop>/stop.json`, `<stop>/tileset.json`, `<stop>/mesh/stop.glb`, `<stop>/pano/` | pilot stop: panorama + 360° mesh (8 colour NLFC wedges, decimated to 10% → 345k triangles, 27 MB GLB, 3D Tiles 1.1 tileset); `pc_align` pending |
| L6 | `layers/l6_radiation_atmo/build.py` | `l6_radiation_atmo/jezero/v1/` (MEDA daily + 5-min, summary) and `l6_radiation_atmo/gale/v1/` (RAD daily, summary) | built per file in one tar pass (~300 MB RAM) |

Every `layer.json` is schema 1.0, written through `common.layerjson` and checked with
`common.validate` before `validated` is set (`python -m common.validate <folder>` to re-check).

Tests live in `tests/rover/` and run with the rest: `pytest -q` and `ruff check .` (same as CI).
Tests on built layers skip when the layer is not built locally.

### L3 cross-check against MMGIS waypoints

- Horizontal: identical to 1 cm on 675 matched waypoints (MMGIS is derived from PLACES, so this
  checks our join and conversion, not PLACES accuracy).
- Height above areoid: identical to 0.5 mm. Height above the sphere differs by 0.5 m median,
  4.6 m p95: MMGIS uses a different areoid radius. **Real check pending: poses against A's L2 terrain.**
- Not matched: 32 waypoints after sol 1897 (PLACES release ends there), 1 other.
- PLACES stores an identity attitude for site 1 (landing, sols 0–12). The pilot stop takes its
  attitude from the image label (ROVER_NAV_FRAME vs SITE_FRAME), which agrees with MMGIS to 0.01°.
- best_interp has no drive-0 rows; they are added from SITE frame origins (88 rows).

### MSL RAD

`raw/l6_radiation_atmo/rad/rad_dose.csv`: 84,812 observations (~16 min each) from every 10th sol,
sols 1–4837. Median 225 µGy/day; follows the solar cycle (≈280 at the 2019–20 minimum, ≈167 in the
2024–25 maximum). Since ~sol 1000 every second observation block carries no dosimetry (empty dose
columns) — that is the instrument's observation pattern, not a parsing gap.

## Interface notes for Person A (please confirm or push back)

1. **`attitude_quat` = rover → Mars-fixed XYZ, `[x, y, z, w]`** (order from the contract example
   `[0, 0, 0, 1]`). The contract does not name the frame; please confirm Mars-fixed. The PLACES-native
   rover → local north-east-down quaternion is kept in the L3 table as `attitude_quat_ned`.
2. **Stop record extras**: `pose_sol` (sol of the pose; `sol` is the sol the panorama was taken).
   Everything else follows the plan's JSON exactly.
3. **Panorama format**: `pano.json` + cubemap tiles `{level}/{face}_{y}_{x}.jpg`, 512 px tiles,
   faces f/r/b/l/u/d = north/east/south/west/up/down, image x right, y down; plus a full
   `equirect.png` (RGBA, column 0 = north). Easy to re-tile for Pannellum/Marzipano/three.js once
   the viewer is chosen.
4. **Eye position ≠ rover position**: the Navcam projection origin is ~1.9 m above and ~1 m off the
   rover nav-frame origin. `pano.json → camera.position_xyz_m` is where the viewer should put the eye.
5. **Need from A**: the Jezero L2 DTM around the landing site (≥ 100 m box) to run `pc_align` of the
   Navcam 45 m DEM (`N_LRGB_0002_DEM_0010052_ORR_L_45M01CM`) and fill `alignment_rms_m`.

## Open questions (B lane)

- Colour: the CL panorama is used (clean, no grid; CT has tick marks, CG a grid). It is not
  white-balanced beyond what MIPL did.
- Frame seams are visible in the CYL mosaic; acceptable for v1, Landform or our own stitcher later.
- Navcam mesh families for a stop: `NLFC/NRFC` colour wedges covering 360° out to ~100–180 m (used),
  `NLG/NRG` the same geometry in greyscale, `NLF_/NRF_` near-field full-frame wedges (rover deck,
  wheels, <10 m), `NLFT/NRFT` one wedge re-tiled with a 5120×1920 texture.
- Mesh LOD: one GLB per stop for now. A finer LOD (keep 25–100 %) as child tiles is the next step.
- MEDA ground temperature: `GROUND_TEMP` exists only at the calibrated level, so
  `fetch.py --only tirs` streams CAL_TIRS per sol and keeps 1-minute samples (~5 MB/sol → ~80 kB/sol).
