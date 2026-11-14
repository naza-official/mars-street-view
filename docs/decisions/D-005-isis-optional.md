# D-005 ISIS in the build image: optional, off by default

## Context
`docs/plan-person-a.md` (M2) put the ISIS 10.0.0 conda env in the image. In `v0.1.0` it was 5.1 of the 8.97 GB. The planned targets (M3 smoke, M4 L1 from MOLA/MDIM mosaics, M6 L2 from the USGS HiRISE ortho and DTM mosaics, M8 THEMIS mosaics) use no ISIS apps. `pc_align` and `dem_geoid` are ASP tools, and the ASP tarball ships its own ISIS libraries (`/opt/asp/lib/libisis10.0.0.so`). ISIS apps also need SPICE kernels (`ISISDATA`), which are not in the image. ISIS is only needed to process raw HiRISE/CTX images (for example our own DTMs, Gale in M9).

## Options
1. Keep ISIS always (plan as written): 8.97 GB, pulled on every Spot start.
2. Build arg `WITH_ISIS`, default `0`; ISIS variant on demand with tag suffix `-isis`.
3. Drop ISIS entirely; add it back if needed.

## Decision
2026-11-14 (human): option 2.
- `docker build --build-arg WITH_ISIS=1` adds `/opt/conda/envs/isis`; the entrypoint sets `ISISROOT` (and `ISISDATA`, default `/isisdata`) only when that env exists.
- Workflow `image`: tags `v*` build the default (no ISIS) image. `workflow_dispatch` with `with_isis=true` builds `<tag>-isis` / `sha-<sha>-isis`.
- Layer order puts ISIS after ASP and the msv env, so both variants share those layers.
