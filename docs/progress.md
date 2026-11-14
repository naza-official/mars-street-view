# Progress log

Newest session first. Each entry: date, milestone, what changed, evidence (command output), open problems, next step.

## Open decisions
| ID | Question | Status |
|---|---|---|
| D-001 | CRS code written in layer.json (proposed IAU_2015:49900) | open, issue #2, `docs/decisions/D-001-crs-code.md`, rec: keep 49900 as body+datum code |
| D-002 | Terrain tiler for a Mars sphere | open, issue #5, `docs/decisions/D-002-terrain-tiler.md`, rec: A (own mesher → 3D Tiles 1.1 GLB) |
| D-003 | One R2 bucket with prefixes vs two buckets | **decided 2026-11-14 by human: no R2; one AWS S3 bucket `mars-sv-123412369922` (us-west-2)**, issue #3 |
| D-004 | How the build VM pulls the GHCR image | **decided 2026-11-14 by human: package public, verified anonymous pull**, issue #12 |
| D-005 | ISIS in the build image | **decided 2026-11-14 by human: optional, `WITH_ISIS=0` default**, `docs/decisions/D-005-isis-optional.md` |

## Sessions
### 2026-11-14 — M3 Pulumi storage + build_vm, smoke run (status: in progress, branch `m3-infra`)

**Done (commit 378fcc5):**
- `common.storage.put_new` uses `If-None-Match: *` plus `x-amz-meta-sha256`. A retry with identical content is a no-op; different content raises `AlreadyPublished`. Files over 5 GB are refused because there is no multipart path. `publish_dir` uploads `layer.json` last. `common.publish` CLI validates the folder before publishing. Contract issue #15 is open for Person B.
- `pipelines.smoke` writes a synthetic 256x128 global DTM COG (IAU_2015:49915, lon 0-360) plus `layer.json`. Snakefile rules `smoke_build` → `smoke` (publish to `orbital/_smoke/global/v1/`).
- `infra/storage` (stack `main`) contains the bucket (protect, versioning, SSE-S3, public access block), a lifecycle rule (`work/` 14 d), CORS, CloudFront with OAC, a bucket policy (CloudFront read; deny PutObject without If-None-Match on `orbital/*`, `rover/*`; deny non-TLS), `msv-lane-{a,b}-write` policies and log group `/msv/build`. `pulumi preview`: **15 to create**, no errors.
- `infra/build_vm` (stack `job`) contains role/profile `msv-build` with the boundary, an egress-only SG, a launch template (AL2023, IMDSv2 hop 2, gp3 encrypted with delete-on-termination, shutdown=terminate, tags on instance/volume/ENI/spot request), and an EC2 Fleet `instant` Spot with `capacity-optimized-prioritized` over the type × default-subnet list. The job settings come from env (`MSV_TARGET/SITE/RUN_ID/IMAGE_TAG/INSTANCE_TYPES/VOLUME_GB`), falling back to stack config and then defaults. The user-data writes `work/<run_id>/DONE` (JSON status) from an EXIT trap and schedules a 72 h hard stop. Its preview fails with `KeyError: 'bucket'` until the storage stack is up (StackReference), which is expected.
- `scripts/run_job.sh <target> <site>` needs a TTY: pulumi up → poll DONE → destroy in an EXIT trap → instance/volume check. Log goes to `logs/run_job-<run_id>.log`.
- Tests: `ruff check .` clean, `pytest -q` **86 passed**.
- `.venv` needed `pip` installed (uv venv) for Pulumi's pip toolchain.

**Blockers / findings:**
- **vCPU quota is 5** (Spot and On-Demand standard) → D-006 (`docs/decisions/D-006-ec2-vcpu-quota.md`). Recommendation: request 32. The smoke run uses 4-vCPU types.
- The smoke target is not in image v0.2.0, so it needs **v0.3.0** built from this branch (tag push; human approval).
- The agent's permission policy blocked every command touching `scripts/run_job.sh` (chmod, `bash -n`), so the script is committed **without the executable bit and not syntax-checked by the agent**. `logs/` is not yet in `.gitignore`.

**Later the same session:**
- IAM moved to `infra/iam` (human-applied, 9 resources up). CloudFront made optional (`cdn`, default off): `CreateDistribution` returns "Your account must be verified" (AWS Support case needed, RequestID 0a88e3c6-221c-4e7f-baec-164347198b32). Storage stack up (11 resources + bucket policy). Probe: a plain PutObject to `orbital/_policytest/global/v1/` is denied and `work/` is allowed.
- `run_job.sh` fixes: Pulumi on `/dev/tty` (`tee` made it non-interactive); stale locks from killed runs removed by hand.
- **EC2 Fleet `instant` failed twice at CreateFleet (stopped per the two-failure rule):**
  1. `InvalidParameter: TerminateInstancesWithExpiration is not supported for given fleet type` → removed.
  2. `InvalidParameterCombination: The minimum target capacity is only available with single instance fleets or single AZ fleets` (RequestID baa7eef2-171c-4928-9ed3-d5f669beb435). The provider seems to send `MinTargetCapacity` with a multi-type, multi-AZ override list.
  Both times the SG and launch template were created and then destroyed; no instance launched; 0 instances and 0 volumes left.
- Options: (a) keep the Fleet and set `spot_options.single_availability_zone=true` (the Fleet still picks the AZ and the type); (b) replace it with a plain `aws.ec2.Instance` with Spot market options, one type per `up`, with `run_job.sh` retrying the next type on a capacity error. Human chose (a) single AZ.

**Next step (human-gated):** (1) `cd infra/storage && pulumi up`; (2) push tag `v0.3.0` → image workflow; (3) `cd infra/build_vm && pulumi preview`; (4) in a terminal: `MSV_INSTANCE_TYPES=m7i.xlarge,m6i.xlarge,m5.xlarge MSV_VOLUME_GB=50 bash scripts/run_job.sh smoke global`; (5) then run the acceptance checks, log to `docs/runs.md`, open the PR.

### 2026-11-14 — M2 follow-up: M2 done, D-004 decided

- PR #10 merged by human; human set **M2 `done`**.
- D-004 decided: make `ghcr.io/naza-official/mars-sv` public. It can only be changed in the GitHub UI (no API). At the time of this commit an anonymous pull is still refused:
```
$ curl (anonymous token) https://ghcr.io/v2/naza-official/mars-sv/manifests/v0.2.0
anonymous manifest v0.2.0 HTTP 404
```
- Follow-up PR merged on the human's instruction without waiting for checks.

Update: human set the package public. Anonymous pull verified (empty `DOCKER_CONFIG`, `docker manifest inspect ...:v0.2.0` exit 0; registry request with `Accept: application/vnd.oci.image.manifest.v1+json` returns HTTP 200 for v0.2.0 and v0.1.0). **Correction:** the earlier 404 "private" checks (here and in the M2 entry) sent the wrong Accept header; GHCR returns `MANIFEST_UNKNOWN` (404) for an OCI manifest without `application/vnd.oci.image.manifest.v1+json`. For anonymous checks use `docker manifest inspect` with an empty `DOCKER_CONFIG`.

Next step: M3 (Pulumi storage + build_vm, smoke run) with image `v0.2.0`.

### 2026-11-14 — M2 Container image on GHCR (status: review, PR #10)

Branch `m2-container-image`. Image `ghcr.io/naza-official/mars-sv:v0.1.0` (= `sha-e65d8ed` = `latest`), digest `sha256:11ef1e286fac9ab274aa9ea1116d812592b6e9abc7a7d41ac96373c6176d2def`. Decision issue #12 (D-004).

**What changed:**
- `docker/Dockerfile`: `condaforge/miniforge3:26.9.4-0`, **linux/amd64 only** (ISIS has no linux-aarch64 build; the build VM is x86). Layers: apt runtime libs (ASP needs `libx11-6 libxau6 libxext6 libsm6 libice6`); conda env `isis` (`isis=10.0.0` from `usgs-astrogeology`, minus `include/` and `*.a`: saves 0.66 GB); ASP 3.7.0 release tarball (`StereoPipeline-3.7.0-2026-06-08-x86_64-Linux`, sha256-checked) in `/opt/asp`; conda env `msv` (`docker/env-msv.yml`: python 3.11, gdal 3.10, rasterio 1.4, pyproj 3.7, numpy 2.2, pystac(-client), boto3, jsonschema; snakemake 9.27.1 from pip because conda-forge doesn't carry it, only bioconda); repo `common`, `pipelines`, `configs` in `/opt/msv`, `pip install .[tiler]`.
- PATH order: `msv` env, then base conda, then ASP `bin`. ISIS is **not** on PATH (it has its own python/GDAL); `ISISROOT=/opt/conda/envs/isis`, `ISISDATA=/isisdata` (empty, mount at runtime). The ASP wrappers unset GDAL/PROJ env, so the msv GDAL doesn't break them.
- `docker/entrypoint.sh`: `run <target>` → `snakemake --cores all --rerun-incomplete --directory /work/<run_id> --config site= run_id= bucket=`, then `python -m pipelines.sync_work` uploads `/work/<run_id>` to `s3://$MSV_BUCKET/work/<run_id>/` (skips `.snakemake/`, refuses keys outside `work/`). Env: `MSV_SITE` (required), `MSV_RUN_ID`, `MSV_BUCKET`, `MSV_SYNC`. Any other argv is exec'd as is.
- `pipelines/Snakefile`: `configfile` now resolved from `workflow.basedir`, so it works with `--directory`.
- `pyproject.toml`: `tiler` extra (spike deps: py3dtiles 12.1.1, quantized-mesh-encoder 0.5.0, pydelatin 0.4.0, pygltflib, scipy). Covers D-002 options A to C until the decision lands.
- `.github/workflows/image.yml`: on PRs touching docker/common/pipelines it builds and smoke-tests without pushing; on `v*` tags (and manual dispatch) it pushes to GHCR. Clean-runner build time 8 min.
- `scripts/check_dem_geoid.py`, `tests/pipelines/test_sync_work.py` (3 tests; suite 61).
- `docs/decisions/D-004-image-pull.md`.

**Findings worth keeping:**
- **`dem_geoid` verified against the Python areoid** (open from M1): on `tests/fixtures/mola_jezero.tif`, `--geoid mola --reverse-adjustment` minus `common.datum.areoid_offset` = mean 0.024 m, max |0.84| m over 500 px. ASP warns `Unknown datum: Mars (2015) - Sphere. Guessing: D_MARS.` That guess is correct (3,396,190 m sphere), but watch for it if a DEM's CRS changes. ASP uses its bundled `/opt/asp/share/geoids/mola_areoid.tif`.
- ASP 3.7.0 also ships aarch64-Linux tarballs, but ISIS 10 doesn't, so the image can't be multi-arch while it includes ISIS.
- ISIS is 5.1 GB of the 8.97 GB. If M4/M6 never call ISIS apps (they use pre-made mosaics and DTMs), a `WITH_ISIS=0` build arg would cut the image to about 3.9 GB. Not done: the plan asks for ISIS.
- The GHCR package is **private** (anonymous manifest request returns 404) even though the repo is public → D-004.

**Evidence (acceptance, against the published image after `docker rmi` of the local build and `docker pull`):**
```
$ docker run --rm ghcr.io/naza-official/mars-sv:v0.1.0 python -m common.validate --help
usage: python -m common.validate [-h] target

Validate a COG, a layer.json / catalog index, or a layer folder (local path or
s3:// URI).
exit 0
$ docker run --rm ghcr.io/naza-official/mars-sv:v0.1.0 pc_align --help
Usage: /opt/asp/libexec/pc_align --max-displacement arg [other options] <reference cloud> <source cloud> -o <output prefix>

  [ASP 3.7.0]
  Build date: 2026-06-08
exit 0
$ docker run --rm ghcr.io/naza-official/mars-sv:v0.1.0 snakemake --version
9.27.1
exit 0
```
Image size: **8.97 GB uncompressed** (8,972,996,581 bytes), **2.91 GB compressed** (sum of layer sizes in the manifest, 2,912,806,593 bytes).

Extra checks (local `mars-sv:dev` build, same Dockerfile):
```
$ docker run --rm mars-sv:dev dem_geoid --help        -> exit 0, [ASP 3.7.0]
$ docker run --rm -v $PWD:/repo -w /repo mars-sv:dev python scripts/check_dem_geoid.py tests/fixtures/mola_jezero.tif
{"n": 500, "mean_m": 0.02383300781261278, "max_abs_m": 0.8413085937502274}
exit 0
$ ... python -c 'import py3dtiles,pydelatin,pygltflib,quantized_mesh_encoder,rasterio'  -> tiler ok, rasterio gdal 3.10.3
$ ... cam2map -help (ISISROOT set)                     -> exit 0
$ docker run --rm -e MSV_SITE=jezero -e MSV_RUN_ID=t1 -e MSV_SYNC=0 mars-sv:dev run all
1 of 1 steps (100%) done
exit 0
$ ruff check .        -> All checks passed!
$ pytest -q           -> 61 passed
```
CI: PR #10 `ci` success (43608466756), `image` build+smoke success (43608466744, 7m59s); tag `v0.1.0` `image` build+smoke+push success (43612508018).

**Open problems:**
- D-004 (#12): the build VM can't pull the private package anonymously. Needs a decision before M3 user-data.
- `run <target>` sync path tested with `MSV_SYNC=0` only; the S3 upload is unit-tested with a mock. First real use is the M3 smoke run.
- No cloud resources used this session; nothing to log in `docs/runs.md`.

**Follow-up (same session): ISIS optional (D-005, decided by human).**
- `docker/Dockerfile`: build arg `WITH_ISIS` (default `0`); ISIS layer moved after ASP and msv so both variants share them. The entrypoint sets `ISISROOT`/`ISISDATA` only when `/opt/conda/envs/isis` exists (ASP wrappers set their own `ISISROOT`).
- `image` workflow: `v*` tags build the default image; `workflow_dispatch` with `with_isis=true` builds `<ref>-isis`.
- Local builds:
```
mars-sv:slim (default)    3,897,726,003 bytes   ISISROOT=unset              envs: msv
mars-sv:isis (WITH_ISIS=1) 8,972,893,630 bytes  ISISROOT=/opt/conda/envs/isis  envs: isis msv
both: common.validate --help, pc_align --help, snakemake --version, dem_geoid --help -> exit 0
isis: cam2map -help -> exit 0
slim: check_dem_geoid.py -> {"n": 500, "mean_m": 0.0238, "max_abs_m": 0.8413}; run all -> 1 of 1 steps done
```
- Published **`ghcr.io/naza-official/mars-sv:v0.2.0`** (= `sha-350f3de` = `latest`), digest `sha256:bd6ebe452a47c89e9ce453b9169fd83d6307af59ec309b99246c13817e4c118c`, workflow run 43615695340 (tag pushed with human approval). This is the image for M3. `v0.1.0` (ISIS included) stays on GHCR.
- Acceptance re-run against the pulled `v0.2.0` (local images removed first):
```
$ docker run --rm ghcr.io/naza-official/mars-sv:v0.2.0 python -m common.validate --help
usage: python -m common.validate [-h] target
exit 0
$ docker run --rm ghcr.io/naza-official/mars-sv:v0.2.0 pc_align --help
Usage: /opt/asp/libexec/pc_align --max-displacement arg [other options] <reference cloud> <source cloud> -o <output prefix>
  [ASP 3.7.0]
exit 0
$ docker run --rm ghcr.io/naza-official/mars-sv:v0.2.0 snakemake --version
9.27.1
exit 0
dem_geoid --help exit 0; ISISROOT=unset; envs: msv
```
- **Image size v0.2.0: 3.90 GB uncompressed (3,897,754,607 bytes), 1.31 GB compressed (1,309,750,149 bytes).** The CI smoke step's `docker image inspect .Size` printed 1.31 GB on this run but 8.97 GB (uncompressed) on the v0.1.0 run, so the runner's Docker seems to have switched to reporting compressed size. Trust the numbers above, not that CI line.

**Next step:** human reviews PR #10, decides D-004, sets M2 `done`. Then M3 (Pulumi storage + build_vm stacks, smoke run) with image `v0.2.0`.

### 2026-11-14 — M1 follow-up: M1 done, storage R2 → S3

- Human set **M1 `done`** (PR #7 merged 2026-11-14T14:38Z).
- Human decision: **no Cloudflare R2**; outputs go to one AWS S3 bucket `mars-sv-123412369922` in `us-west-2`. The account-suffixed name avoids global-name squatting; `mars-sv` was free (HeadBucket 404) but could be claimed by anyone before M3.
- `docs/milestones.json`: M1 → `done`; M3 gate no longer needs a Cloudflare token; acceptance URIs `r2://mars-sv/...` → `s3://mars-sv-123412369922/...`.
- `docs/contracts.md`: bucket layout header now S3; IAM per-lane prefix rule; changelog entry. Schema unchanged (layer.json only has relative paths). Needs Person B's review (contract issue).
- `common/storage.py`: R2 config, env keys and SSM key lookup removed; plain boto3 S3 client in `us-west-2` with the default credential chain; only `s3://` URIs. `common/validate.py` adjusted. Tests rewritten.
- `CLAUDE.md`, `docs/plan-person-a.md` (M3 `infra/storage`, build VM role, M5 deploy via S3 + CloudFront), `docs/decisions/D-003` updated.

Evidence:
```
$ ruff check .
All checks passed!
$ pytest -q
58 passed
```

Open: Person B needs AWS access to write `rover/*` (IAM role in this account or cross-account). Decide in M3.

Next step: M2 (container image on GHCR).

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
