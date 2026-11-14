# Person A plan: orbital layers and viewer

Read only the section for the milestone you are working on. Acceptance checks live in `docs/milestones.json`.

## Context

**Goal.** One Mars model in layers: a whole-planet globe (L1), sharper terrain where orbital stereo exists (L2), thermal overlays (L5), and a web viewer (L8). Person B builds rover poses (L3), street-view panoramas (L4), radiation and atmosphere (L6) and science overlays (L7).

**Pilot site.** Jezero crater, centre about 18.4 N, 77.5 E. Gale crater comes later.

**Budget and style.** Pet project, near-zero viewer traffic. Build in 2 to 3 day bursts on Spot EC2, tear everything down, keep only `out/` in Cloudflare R2. Target total cloud spend through M8: under $40.

**Environment.**
| Item | Value |
|---|---|
| AWS account | `123412369922`, region `us-west-2`, profile `claude-agent` (SSO, 4 h sessions) |
| IAM limits | PowerUserAccess; roles only `msv-*` with boundary `agent-boundary`; no other regions |
| Pulumi state | `s3://mars-sv-pulumi-state-123412369922`, secrets via `awskms://alias/pulumi` |
| Output storage | Cloudflare R2 bucket `mars-sv` (created in M3) |
| Image registry | `ghcr.io/naza-official/mars-sv` |
| Language | Python 3.11 for pipeline and infra (Pulumi Python); TypeScript for the viewer |

**Key sources.**
| Dataset | Use | Link |
|---|---|---|
| Viking MDIM 2.1 colorized, 232 m | L1 imagery | https://astrogeology.usgs.gov/search/map/mars_viking_colorized_global_mosaic_232m |
| MOLA MEGDR DEM, 463 m | L1 terrain | https://astrogeology.usgs.gov/search/map/mars_mgs_mola_dem_463m |
| Jezero HiRISE ortho mosaic, 25 cm | L2 imagery | https://astrogeology.usgs.gov/search/map/Mars/Mars2020/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first |
| Jezero HiRISE DTM mosaics | L2 terrain | https://astrogeology.usgs.gov/search/map/mars-sample-return-terrain-relative-navigation-hirise-dtm-mosaic |
| USGS STAC (CTX, HiRISE, THEMIS) | bbox queries | https://stac.astrogeology.usgs.gov/docs/ |
| THEMIS controlled Day/Night IR | L5 | https://stac.astrogeology.usgs.gov/docs/data/mars/themis_controlled_mosaics/ |
| THEMIS quantitative thermal inertia | L5 | https://astrogeology.usgs.gov/search/map/themis_thermal_inertia_mosaic_quantitative_30s060e_100mpp |
| Jezero thermal inertia (Zenodo) | L5 | https://zenodo.org/record/4931348 |

Verify each URL and the current STAC API endpoint before coding against it; record what you used in `layer.json` `sources`.

**Known risks.**
- Most terrain tilers assume Earth's WGS84 ellipsoid. M1 includes a spike to pick a tiler that works on a Mars sphere.
- MDIM's label uses an ellipsoid; convert once at ingest and check Jezero does not shift by more than one 232 m pixel.
- Spot interruptions: every Snakemake step must be restartable and write to `work/<run_id>/`.
- ASP and ISIS are large; keep them in one image and pin versions.

## M0 Repo bootstrap
- Create the layout:
```
common/  layers/l1_global/  layers/l2_regional/  layers/l5_thermal/
viewer/  pipelines/Snakefile  infra/storage/  infra/build_vm/
configs/sites/jezero.yaml  tests/fixtures/  docs/  scripts/
```
- `pyproject.toml` (package `common`, ruff, pytest), `environment.yml` (python 3.11, gdal, rasterio, pyproj, pystac, pystac-client, snakemake, boto3, jsonschema).
- `.github/workflows/ci.yml`: ruff + pytest on push and PR.
- `configs/sites/jezero.yaml`: name, bbox `[77.2, 18.2, 77.7, 18.7]`, centre, local CRS parameters.

## M1 L0 common library and terrain-tiler spike
Coordinate with Person B: open a `contract` issue linking the PR.
- `common/crs.py`: Mars sphere definitions as pyproj CRS objects (geographic, simple cylindrical 0 to 360 E, site equirectangular from a site config), and a Mars-fixed XYZ transformer.
- `common/datum.py`: wrapper around ASP `dem_geoid` (areoid to sphere), plus a pure-Python check comparing a sample of points.
- `common/cog.py`: write COG with overviews, compression, nodata, tags.
- `common/layerjson.py`: JSON Schema for `layer.json` and `catalog/index.json`; read, write, validate.
- `common/validate.py`: CLI; checks CRS, bounds, nodata, COG structure, `layer.json` against schema. Accepts local paths and `s3://` or `r2://` URIs.
- `common/storage.py`: R2 client (S3-compatible via boto3) reading endpoint and keys from env or SSM.
- Fixtures: 256x256 crops of MDIM and MOLA around Jezero, under 5 MB total.
- **Spike:** try 2 to 3 approaches on a 512x512 MOLA crop: `py3dtiles` or a similar library, a custom glTF mesher (grid to triangles in Mars-fixed XYZ with a tile-local origin), and any tiler that accepts a custom ellipsoid. Measure correctness (heights on the sphere), tile count and size, and whether 3DTilesRendererJS loads them. Write `docs/decisions/D-002-terrain-tiler.md` with a recommendation; the human decides.

## M2 Container image
- `docker/Dockerfile` from `condaforge/miniforge3`: `isis` from channel `usgs-astrogeology`, ASP release tarball (pin version), GDAL, the chosen tiler, the `common` package, Snakemake.
- `docker/entrypoint.sh`: runs `snakemake --cores all <target>` with config from env, then syncs outputs.
- GitHub Actions workflow builds on tag `v*` and pushes to GHCR.
- Do not bake ISIS data areas into the image; download only the kernels needed at runtime, if any.

## M3 Infrastructure with Pulumi
Two stacks under `infra/`, Pulumi Python, state in the S3 backend.
- `infra/storage`: Cloudflare R2 bucket `mars-sv` (provider `pulumi-cloudflare`, token from Pulumi config secret `cloudflare:apiToken` set by the human), R2 access keys stored in SSM Parameter Store as SecureString `/msv/r2/*`.
- `infra/build_vm`, parameterised by `target`, `site`, `instance_type`, `volume_gb`, `image_tag`:
  - Spot instance in `us-west-2`, default `m7i.4xlarge`, fallbacks `m6i.4xlarge`, `r6i.2xlarge`.
  - gp3 root volume 500 GB, `delete_on_termination=true`.
  - IMDSv2 required; no SSH key; access through SSM Session Manager.
  - Instance role `msv-build` with boundary `agent-boundary`; permissions: read SSM `/msv/*`, read USGS public buckets, write CloudWatch logs.
  - `instance_initiated_shutdown_behavior = terminate`; user-data installs Docker, pulls the image, runs the target, syncs to R2, writes `work/<run_id>/DONE`, then `shutdown -h now`.
  - Safety: user-data also schedules `shutdown -h +4320` (72 h) as a hard stop.
  - Tags on everything: `project=mars-sv`, `milestone`, `run_id`.
- `scripts/run_job.sh <target> <site>`: `pulumi up` (asks human) then polls `work/<run_id>/DONE`, then `pulumi destroy` (asks human).
- Smoke target `smoke` writes `orbital/_smoke/global/v1/layer.json`.

## M4 L1 global base
Snakemake rules in `layers/l1_global/`:
1. Fetch MDIM 2.1 colour and MOLA 463 m (stream if COG, else download to VM).
2. Reproject MDIM to the sphere simple cylindrical CRS; record the measured shift at Jezero.
3. Convert MOLA heights to height above the sphere.
4. Build terrain tiles with the tiler chosen in D-002; drape MDIM as textures.
5. Validate, write `layer.json`, publish `orbital/l1_global/global/v1/`, update `catalog/index.json`.

## M5 Viewer skeleton
- `viewer/`: Vite + TypeScript + three.js + 3DTilesRendererJS.
- Mars sphere radius 3,396,190 m; camera controls for globe and close-up.
- Loads `catalog/index.json`, then each layer's `layer.json`, then tilesets.
- Site buttons fly to Jezero and Gale; show lon, lat, height under the cursor.
- Deploy as a static site (Cloudflare Pages or R2 public bucket) via a GitHub Actions workflow.

## M6 L2 Jezero terrain (deadline for Person B)
1. Query STAC by the Jezero bbox for HiRISE DTMs and orthos and nearby CTX DTMs; log chosen product IDs.
2. Align with ASP `pc_align`: HiRISE to CTX, CTX to MOLA. Save RMS per step to `alignment_report.json`.
3. Convert heights to the sphere with `dem_geoid`.
4. Write `dtm.tif` (1 m) and `ortho.tif` (25 cm) as COGs in the Jezero local CRS.
5. Build tiles that replace L1 inside the Jezero footprint; check the seam.
6. Validate, publish `orbital/l2_regional/jezero/v1/`, update the catalog, open an issue for Person B with paths and the alignment report.

## M7 Integration gate with Person B
- Viewer reads `rover/l3_poses/jezero/vN/stops.geojson` and stop records.
- Street-view mode: click a stop to open its panorama aligned to the stop pose; arrow to `next_stops`.
- Hide stops with `alignment_rms_m` above threshold.
- Gate: one Jezero stop's panorama lines up with L2 terrain. The human confirms with a screenshot.

## M8 L5 thermal
1. Check the licence of the Zenodo Jezero file; stop and open a `decision` issue if unclear.
2. Convert THEMIS TI (tile covering Jezero), Day and Night IR for Syrtis Major, and Jezero TI to float32 COGs on the shared grid.
3. Viewer: overlay toggle, colour ramp with legend, value on hover.
4. Small instance is enough (`m7i.xlarge`).

## M9 Later
Gale L2 (same pipeline as M6), viewer polish, performance.
