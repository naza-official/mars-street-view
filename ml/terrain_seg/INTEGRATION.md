# Terrain-aware routing: integration guide (Person A <-> Person B)

**What it is.** Given two lon/lat points, the route service returns a least-cost rover route that
avoids steep slopes and costly terrain (sand, boulder fields). Terrain classes come from Person B's
rover-trained vector database; imagery and heights come from Person A's L2 layer. Classification
runs live only for the tiles a route needs and is cached, so repeated or overlapping routes are
instant.

```
Person A (orbital)                         Person B (rover)
orbital/l2_regional/<site>/vN/             rover/l9_terrain_db/<site>/vN/
  ortho.tif  dtm.tif  layer.json             vector_db.npz  db.json  layer.json
          \                                   /
           \                                 /
            plan_route(l2, start, end, width)   <- ml/terrain_seg/corridor.py
                |  per 256 m tile: cached? -> load : ortho window -> MOMO -> kNN -> cache
                |  slope from dtm, cost = class cost x slope penalty, least-cost path
                v
     <name>.geojson (route, lon/lat)   <name>.tif (class + cost COG, L2 grid)   <name>.json
                |
         Person A's viewer draws the route and (optionally) the class overlay
```

## 1. What Person A provides: the L2 layer

The service reads the layer **only through its `layer.json`** (local folder or `s3://` URI, via
`common.validate.Target`, so `common.storage` credentials and GDAL `/vsis3/` work unchanged).

| Requirement | Value |
|---|---|
| `layer.json` | schema 1.0 (`common.layerjson`), `layer = l2_regional` |
| asset `ortho` | COG, uint8, 1 band (grey) or 3 bands (RGB), nodata 0, **0.25 m** HiRISE or finer (it is resampled to 0.25 m; the database was built at 0.25 m) |
| asset `dtm` | COG, float32, metres above the 3,396,190 m sphere, nodata set (used only for slope) |
| CRS | the site CRS (`common.crs.site_crs`), the same for both assets |

This is exactly the L2 row of `docs/contracts.md`; no new requirement on Person A beyond the two
asset names `ortho` and `dtm` (already the names in the contract's `layer.json` example).
Outside HiRISE coverage (CTX only) the database does not apply: route on slope alone there.

## 2. What Person B provides

**a) The vector database layer** `rover/l9_terrain_db/<site>/vN/` (built by `package_db.py`):

| Asset | Content |
|---|---|
| `vector_db.npz` | `embedding` N x 768 (float16, L2-normalised MOMO patch tokens), `label` 0..3, `area`, `patch_id` |
| `db.json` | classes, model id/URL/licence, `k`, `unknown_threshold_cosine`, counts, evaluation |
| `layer.json` | schema 1.0, owner B |

**b) The service** `ml/terrain_seg/corridor.py`:

```python
from corridor import plan_route
res = plan_route(
    l2="s3://mars-sv-123412369922/orbital/l2_regional/jezero/v1",   # or a local folder
    start_lonlat=(77.45088, 18.44463), end_lonlat=(77.44229, 18.43358),
    width_m=300, name="demo",
    db_dir="/path/to/rover/l9_terrain_db/jezero/v1",
    cache_root="/path/to/route_cache", out_dir="/path/to/routes",
)
res["geojson"], res["cog"], res["length_m"], res["class_share"], res["computed"], res["cached"]
```
CLI: `python ml/terrain_seg/corridor.py --l2 <folder|s3://...> --start LON LAT --end LON LAT
--width 300 --name demo [--db ...] [--cache ...] [--out ...] [--max-slope 25]`

**c) Outputs** (`<out>/<name>.*`):

| File | Content |
|---|---|
| `.geojson` | `LineString` in lon/lat (IAU_2015:49900); properties: `length_m`, `straight_m`, `class_share`, `l2`, `database`, `class_cost`, `max_slope_deg` |
| `.tif` | COG on the L2 CRS, 4 m cells, nodata -1. Band 1: class (0 soil, 1 bedrock, 2 sand, 3 big rock, 4 unknown). Band 2: cost (-1 = blocked). Tags: `CLASS_LEGEND`, `CLASS_COST`, `L2`, `DATABASE`. Passes `common.validate`. |
| `.json` | run summary: tiles total / computed live / from cache, timings |
| `.png` | preview (ortho, classes, route, straight line) |

## 3. Live + cache

- Tile grid: fixed 256 m squares in the L2 CRS (`floor(x / 256)`, `floor(y / 256)`), each with a
  28 m context margin for the embedding model.
- Cache key: `<cache_root>/<L2 layer id>/<database id>/tile_<i>_<j>.npz` where the L2 id is
  `l2_regional_<site>_<vN>` and the database id includes a hash of `vector_db.npz`. A new L2
  version or a rebuilt database therefore never reuses old tiles; old folders can be deleted.
- Proposed bucket location for a shared cache: `work/route_cache/...` (deletable by contract).
- Measured on the demo route (landing -> Seitah edge, 814 m straight, 11 tiles, RTX 4060):
  23.5 s live from local COGs, 0.5 s from cache; ~0.2 MB per tile.

## 4. How the viewer uses it

1. User picks two points -> backend calls `plan_route` -> viewer draws `<name>.geojson`.
2. Optional overlay: the class band of `<name>.tif` with the legend colours
   (soil `#D7A05A`, bedrock `#3C78E6`, sand `#FADC28`, big rock `#E62828`, unknown `#5A5A5A`).
3. Who runs `plan_route` is open (see section 6): a GPU worker on AWS (live), or precomputed
   routes published as files (no backend needed for a static viewer).

## 5. Environment

- Python 3.11+, `pip install -e .` (common) plus `ml/terrain_seg/requirements.txt`
  (torch, timm, transformers, scikit-image, scipy, pillow, rasterio).
- GPU recommended (CUDA, ~1 GB VRAM); CPU works but a live tile takes tens of seconds.
- MOMO weights: `vit-b-16/momo.pth` from https://huggingface.co/Mirali33/MOMO (CC-BY-4.0) at
  `$MSV_DATA/models/momo/momo_vitb16.pth`. The checkpoint is a pickle; it is loaded with
  `weights_only=True` and only `argparse.Namespace` allowlisted, so no code from it runs.

## 6. Open points for a `contract` issue

1. Layer name and layout of the database: `rover/l9_terrain_db/<site>/vN/` with assets
   `vector_db` (.npz) and `meta` (db.json).
2. Class legend and cost table (current placeholders: soil 1, bedrock 1.5, sand 3, big rock 8,
   unknown 2.5) and what "unknown" should mean for routing.
3. Output grid: 4 m cells on the L2 CRS - acceptable for the viewer overlay?
4. Where the shared tile cache lives (`work/route_cache/`?) and who may write it.
5. Who runs the service: live GPU worker vs precomputed demo routes.
6. Route endpoints in lon/lat (IAU_2015:49900) - confirm.

## 7. Testing the integration without Person A's data

`make_test_l2.py` builds a stand-in L2 layer in exactly the format of section 1 from PLACES data
(written under `$MSV_DATA/test_orbital/`, marked as a test, never published):

```bash
python ml/terrain_seg/make_test_l2.py                     # ortho 0.25 m + dtm 1 m, validated
python ml/terrain_seg/package_db.py                       # rover/l9_terrain_db/jezero/v1
python ml/terrain_seg/corridor.py --l2 ~/mars-data/test_orbital/l2_regional/jezero/v1 \
    --start 77.45088 18.44463 --end 77.44229 18.43358 --width 300 --name test
python -m common.validate ~/mars-data/work/terrain_seg/routes/test.tif
```
Expected: route ~977 m (straight 814 m), ~79 % soil, 11 tiles live on the first run and 11 from
cache on the second, `cog_valid: true`. When the real L2 exists, pass its folder or `s3://` URI
to `--l2`; nothing else changes. `--l2 places` runs the same pipeline on the PLACES map (demo).

## 8. Known limitations

- Terrain classes come from a model trained on Curiosity images and transferred to Perseverance:
  sand transfers well between areas, bedrock is often missed (see README, leave-one-area-out).
- Patch-level classes (4 m) are noisy; neighbouring tiles are classified independently.
- The database covers five Jezero areas; elsewhere expect more "unknown".
