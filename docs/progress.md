# Progress log

Newest session first. Each entry: date, milestone, what changed, evidence (command output), open problems, next step.

## Open decisions
| ID | Question | Status |
|---|---|---|
| D-001 | CRS code written in layer.json (proposed IAU_2015:49900) | open, issue #2, `docs/decisions/D-001-crs-code.md`, rec: keep 49900 as body+datum code |
| D-002 | Terrain tiler for a Mars sphere | open, issue #5, `docs/decisions/D-002-terrain-tiler.md`, rec: A (own mesher → 3D Tiles 1.1 GLB) |
| D-003 | One R2 bucket with prefixes vs two buckets | open, issue #3, `docs/decisions/D-003-r2-buckets.md`, rec: one bucket + write guard in `common/storage.py` |

## Sessions
### 2026-11-14 — M1 L0 common library + terrain-tiler spike (status: review)

Branch `m1-l0-common`. Contract review issue #6 (Person B), decision issue #5 (D-002).

**What changed:**
- `common/crs.py`: geographic `IAU_2015:49900`; global simple cylindrical `IAU_2015:49915` (sphere eqc, clon 180 = contract "0 to 360 E"); `site_crs(site)` eqc on 49900; Mars-fixed XYZ (geocentric on the 49900 datum) + numpy `lonlat_to_xyz`; `is_mars_sphere`, `base_authority` (axis-order-insensitive, so it survives a GeoTIFF round trip).
- `common/datum.py`: `dem_geoid --geoid mola --reverse-adjustment` wrapper (output `<prefix>-adj.tif`, **not run yet: ASP not installed locally; verify in M2 image**); pure-Python areoid from PDS `mega90n000cb.img` (4 ppd, 2 MB, in `raw/areoid/`, bilinear, lon wrap); `check_adjustment` samples pixels and compares the height change against the areoid offset.
- `common/cog.py`: COG writer (DEFLATE, predictor 3/2, 512 blocks, average overviews, tags).
- `common/layerjson.py`: JSON Schemas for layer.json and catalog/index.json, read/write/validate, `--check` CLI.
- `common/validate.py`: CLI for a COG, a JSON doc, or a layer folder (local, `s3://`, `r2://`). Checks Mars sphere + 49900 authority, nodata, dtype, COG layout, tiling, overviews, lon/lat bounds, elevation range ±30 km, thermal UNITS, assets present, raster bounds inside layer bounds.
- `common/storage.py`: R2 / S3 client; keys from env `R2_*` or SSM `/msv/r2/*`; repr hides secrets; GDAL `/vsis3` env for R2.
- `scripts/make_fixtures.py`: crops by HTTP range read from planetarymaps.usgs.gov (no archive download). Fixtures: `mola_jezero.tif` 92 KB, `mdim_jezero.tif` 140 KB (total 0.23 MB < 5 MB), plus `layer_example.json`, `index_example.json`.
- Tests: `tests/common/` (53 tests); full suite 58.
- Spike: `spikes/terrain_tiler/` + `docs/decisions/D-002-terrain-tiler.md`. Outputs in `raw/spike/` (not in Git).
- D-001 doc updated with GDAL 3.10.3 evidence.
- `pyproject.toml` deps: numpy, pyproj, rasterio, jsonschema, boto3 (rasterio wheel bundles GDAL, so CI pip works).

**Findings worth keeping:**
- MOLA and MDIM global mosaics are not in `s3://astrogeo-ard` or the USGS STAC API (18 collections, none global). Used `https://planetarymaps.usgs.gov/mosaic/...` with range reads. Both files are strip-organised (1 row per strip) and uncompressed, so windowed reads are cheap.
- **USGS MOLA GeoTIFF georeferencing is off.** Pixel size is 463.0935 m, but 2πR/46080 = 463.0836 m. At Jezero the GeoTIFF transform puts pixels 99 m east and 24 m north of the exact 128 ppd grid, growing to ~230 m at ±180°. MDIM's transform is exact (256 ppd). Fixtures use the exact ppd grid. M4 must do the same, not trust MOLA's transform.
- MDIM GeoTIFF CRS is already the 3,396,190 m sphere (the "ellipsoid in label" risk applies to the PDS label, not this file).
- Areoid offset at Jezero is -1,676 m. Sphere-relative heights reach about -25 km near the poles (areoid flattening), so the validator range is ±30 km.
- pydelatin vertex `y` counts from the bottom row (`h[64 - y, x]`).

**Evidence (acceptance):**
```
$ pytest -q tests/common
.....................................................                    [100%]
53 passed in 0.32s
exit 0
$ python -m common.validate tests/fixtures/mdim_jezero.tif
  crs: Mars (2015) - Sphere / Ocentric / Equirectangular, clon = 180
  bounds_lonlat: [77.0000, 17.8984, 78.0000, 18.8984]
  resolution: 231.542 x 231.542
  size: 256x256x3 uint8 block 512x512 overviews [] DEFLATE
  kind: imagery
ok tests/fixtures/mdim_jezero.tif
exit 0
$ python -m common.validate tests/fixtures/mola_jezero.tif
  crs: Mars (2015) - Sphere / Ocentric / Equirectangular, clon = 180
  bounds_lonlat: [76.5000, 17.3984, 78.5000, 19.3984]
  resolution: 463.084 x 463.084
  size: 256x256x1 float32 block 512x512 overviews [] DEFLATE
  kind: elevation
  height range: -4990.3 .. -2190.3 m
ok tests/fixtures/mola_jezero.tif
exit 0
$ python -m common.layerjson --check tests/fixtures/layer_example.json
ok tests/fixtures/layer_example.json (layer.json, schema 1.0)
exit 0
$ test -f docs/decisions/D-002-terrain-tiler.md
exit 0
```
```
$ ruff check .
All checks passed!
$ pytest -q
58 passed in 0.49s
```
CI on PR #7 (https://github.com/naza-official/mars-street-view/pull/7): `lint-test pass` on both the push and pull_request runs (43605752367, 43605773622).

**Open problems:**
- `dem_geoid` wrapper untested against real ASP (M2).
- meshopt/quantization size for option A not measured: installing `gltfpack` was blocked by the agent permission policy.
- `.claude/hooks/guard.sh` and `.claude/commands/*` still missing (from M0).
- Spike deps (`spikes/terrain_tiler/requirements.txt`) are in `.venv` only, not in `pyproject.toml`.

**Next step:** Person B reviews #6 and the PR; human decides D-002 (#5) and D-001 (#2), merges, sets M1 `done`. Then M2 (container image): include ASP for `dem_geoid` and test `common.datum.run_dem_geoid` against the Python areoid with `check_adjustment`.

### 2026-11-14 — M0 Repo bootstrap (status: review, PR #1)

**Environment setup (before M0):**
- Moved harness docs into `docs/`; replaced `<owner>` with `naza-official` in `docs/contracts.md`, `docs/plan-person-a.md`, `docs/milestones.json` (README_HARNESS step 2, placeholder fill, no contract change).
- Created `.venv` (uv, CPython 3.11.14). Conda `base` is 3.10 and fails init; activate `.venv` before `scripts/init.sh`.
- Bootstrapped cloud (human approved): S3 bucket `mars-sv-pulumi-state-123412369922` (us-west-2, versioned, public access blocked, SSE-S3, tag `project=mars-sv`); KMS key `bb0ceb01-c422-4f36-8798-51562af39590` with alias `alias/pulumi` (~$1/month).
- Pulumi login URL: `s3://mars-sv-pulumi-state-123412369922?region=us-west-2&awssdk=v2&profile=claude-agent` (region/profile in URL so plain `pulumi whoami` works).
- `scripts/init.sh` → `init ok` (run by human; agent permission denied for that script).
- Harness committed on `main` as `4f82daf` and pushed (human approved).

**What changed (branch `m0-repo-bootstrap`):**
- Layout: `common/`, `layers/{l1_global,l2_regional,l5_thermal}/`, `viewer/`, `pipelines/Snakefile`, `infra/{storage,build_vm}/`, `configs/sites/jezero.yaml`, `tests/fixtures/`.
- `pyproject.toml` (package `mars-sv-common`, module `common`, ruff, pytest), `environment.yml` (conda-forge, python 3.11 + gdal, rasterio, pyproj, pystac, pystac-client, snakemake, boto3, jsonschema).
- `common/sites.py` site-config loader + `tests/test_sites.py`.
- `configs/sites/jezero.yaml`: bbox `[77.2, 18.2, 77.7, 18.7]`, centre `[77.5, 18.4]` (plan value), local eqc on the 3,396,190 m sphere. No CRS code written (D-001 open).
- `.github/workflows/ci.yml`: pip install on Python 3.11, `ruff check .`, `pytest -q` on push and PR. Uses pip, not conda, because M0 needs no GDAL; switch to micromamba in M1 when `common` needs rasterio.

**Evidence (local):**
```
$ ruff check .
All checks passed!
$ pytest -q
....                                                                     [100%]
4 passed in 0.11s
$ test -f configs/sites/jezero.yaml
exit 0
$ gh run list --limit 1
completed	success	M0: repo bootstrap	ci	m0-repo-bootstrap	pull_request	43602435295	25s	2026-11-14T13:58:41Z
```

**Follow-up in the same session (after PR opened):**
- Fixed `configs/sites/jezero.yaml`: contract says site CRS is "centred on the site", but `lat_0` was 0 (centre projected to y = 1,090 km). Now `lat_0 = lon/lat centre`, `base_crs: IAU_2015:49900`; added a test.
```
$ pytest -q
5 passed in 0.02s
$ python (Transformer IAU_2015:49900 -> jezero proj4)
centre -> [0.0, 0.0]
SW -> [-16873.3, -11854.9] NE -> [11248.9, 17782.4]
```
- Snakefile dry run (snakemake 9.27.1 installed in `.venv` only, not in pyproject):
```
$ snakemake -n -s pipelines/Snakefile
neither input nor output: all
This was a dry-run (flag -n).
```
- PROJ 9.5.1 check for D-001: `IAU_2015:49900` = "Mars (2015) - Sphere / Ocentric", R = 3396190, lat north / lon east. A site CRS made from a PROJ string loses the IAU authority; one built with `ProjectedCRS(geodetic_crs=CRS("IAU_2015:49900"))` keeps it. Use the latter in `common/crs.py` (M1).
- Wrote `docs/decisions/D-001-crs-code.md`, `D-003-r2-buckets.md`; created labels `decision`, `contract`; opened issues #2 (D-001) and #3 (D-003).

**Open problems:**
- `.claude/hooks/guard.sh` and `.claude/commands/*` from README_HARNESS are missing in the repo.

**Next step:** human reviews PR https://github.com/naza-official/mars-street-view/pull/1, merges, sets M0 `done`. Then M1 (L0 common library + terrain-tiler spike).
