# Contract issue draft — terrain-aware routing (label: `contract`)

**Title:** Contract: terrain-aware routing — terrain DB layer (B), route outputs, use of L2 ortho/DTM

---

## Summary

Person B has a working route service: given two lon/lat points it returns a least-cost rover route
that avoids steep slopes and costly terrain (sand, boulder fields). It reads imagery and heights
**from Person A's L2 layer** through `layer.json`, terrain classes from a new B-lane layer, and
classifies only the 256 m tiles a route needs (live, then cached). Code and guide:
`ml/terrain_seg/corridor.py`, `ml/terrain_seg/INTEGRATION.md` (branch `b/terrain-seg`).

Tested end-to-end against a stand-in L2 layer in exactly the contract format (built from PLACES,
not published): landing -> Seitah edge, 977 m route (814 m straight), 11 tiles, 23.5 s live,
0.6 s from cache, output COG passes `common.validate`.

This issue asks to agree the points below before anything is published to the bucket. No change
to the `layer.json` schema (1.0) is needed; the changes are additions to `docs/contracts.md`.

## 1. What B needs from A (no new work, please confirm)

The L2 layer as already described in `docs/contracts.md`:

| | Required |
|---|---|
| asset `ortho` | COG, uint8, 1 or 3 bands, nodata 0, 0.25 m HiRISE (finer is resampled to 0.25 m) |
| asset `dtm` | COG, float32, metres above the 3,396,190 m sphere, nodata set |
| CRS | site CRS (`common.crs.site_crs`), same for both assets |
| discovery | `orbital/l2_regional/<site>/vN/layer.json`, assets named `ortho` and `dtm` |

**Please confirm:** the asset names `ortho` / `dtm` (as in the contract example) and 0.25 m ortho
resolution for Jezero.

## 2. New B layer: terrain database (proposal)

```
rover/l9_terrain_db/<site>/vN/
  vector_db.npz   embedding N x 768 (float16, L2-normalised MOMO ViT-B/16 patch tokens, 4 x 4 m),
                  label 0..3, area, patch_id
  db.json         classes, model id / URL / licence (MOMO, CC-BY-4.0), k, unknown threshold,
                  counts, evaluation
  layer.json      schema 1.0, owner B, depends_on rover/l3_poses/<site>/vN
```
Immutable `vN` like every other layer; `layer.json` uploaded last after `common.validate`.

## 3. Route outputs (proposal)

Routes are results, not layers. Per request `<name>`:

| File | Content |
|---|---|
| `<name>.geojson` | LineString in lon/lat (IAU_2015:49900); properties `length_m`, `straight_m`, `class_share`, `l2`, `database`, `class_cost`, `max_slope_deg` |
| `<name>.tif` | COG on the L2 CRS, 4 m cells, nodata -1; band 1 class (0 soil, 1 bedrock, 2 sand, 3 big rock, 4 unknown), band 2 cost (-1 blocked); tags `CLASS_LEGEND`, `CLASS_COST`, `L2`, `DATABASE` |

Proposed location for published demo routes: `rover/routes/<site>/vN/<name>.*`.

## 4. Decisions needed

| # | Question | Options | B recommends |
|---|---|---|---|
| D1 | Who runs `plan_route`? | (a) live GPU worker on AWS behind an endpoint the viewer calls; (b) precomputed demo routes published as files | **(b) for v1** (static viewer, no backend), (a) later |
| D2 | Class costs | current placeholders: soil 1, bedrock 1.5, sand 3, big rock 8, unknown 2.5; slope penalty x(1 + (slope/10°)²), blocked > 25° | keep for v1, revisit after first routes |
| D3 | Meaning of "unknown" (terrain unlike anything in the DB) | (a) moderate cost (2.5); (b) blocked; (c) slope-only cost | **(a)** |
| D4 | Shared tile cache | `work/route_cache/<l2 id>/<db id>/tile_i_j.npz` (deletable, keyed by L2 version and DB hash) | yes, writable by both |
| D5 | Output grid for the overlay | 4 m cells on the L2 CRS | yes (patch size of the model) |
| D6 | Route endpoints | lon/lat on IAU_2015:49900 | yes |
| D7 | Areas without HiRISE (CTX only) | route on slope only; DB not applied | yes |

## 5. Proposed additions to `docs/contracts.md`

- **Bucket layout:** add `rover/l9_terrain_db/<site>/vN/` (B writes) and, if D1 = (b),
  `rover/routes/<site>/vN/` (B writes); `work/route_cache/` (both, deletable).
- **Handoffs table:**
  - A to B: L2 `ortho.tif` + `dtm.tif` (already listed) — consumed by the route service.
  - B to A: terrain DB layer + route GeoJSON/COG — drawn by the viewer.
- **Formats table:** "Terrain classes (B) | per-route COG, 4 m, class + cost | GeoJSON route +
  COG overlay".

## 6. Known limitations (for context)

Terrain classes come from a model trained on Curiosity images and transferred to Perseverance via
rover-to-orbit projection; leave-one-area-out over five Jezero areas: sand transfers well
(recall 0.7–0.9), bedrock is often missed. Fine for a demo; not navigation-grade.

---
Review needed from both A and B (contract change). After agreement: update `docs/contracts.md`,
then B publishes `rover/l9_terrain_db/jezero/v1` and demo routes.
