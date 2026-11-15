"""Route service: terrain classes + slope -> least-cost route between two lon/lat points.

Orbital data come from Person A's L2 layer (ortho + DTM COGs, found through layer.json; local folder
or s3:// URI) - or, for the demo only, from the PLACES map (`--l2 places`). The vector database
comes from the B-lane layer rover/l9_terrain_db/<site>/vN/ (package_db.py).

Live + cache: the corridor is covered by tiles of a fixed 256 m grid in the L2 CRS. A tile is
classified only on first use and cached under
    <cache>/<L2 layer id>/<database id>/tile_<i>_<j>.npz
so a new L2 version or a rebuilt database never reuses stale tiles.

Per tile: ortho window (+28 m context) at 0.25 m -> MOMO patch tokens (4 x 4 m) -> per-class
k-nearest search in the database -> class per patch ("unknown" beyond the database threshold).
Cost per 4 m cell = class cost x (1 + (slope / 10 deg)^2); slopes > --max-slope and cells outside
the corridor are blocked; least-cost 8-connected path.

Outputs (<out>/<name>.*):
  geojson   route LineString in lon/lat (IAU_2015:49900) + length and class shares
  tif       COG in the L2 CRS, 4 m: band 1 class (0 soil 1 bedrock 2 sand 3 big rock
            4 unknown, 255 outside), band 2 cost; tags carry legend, costs, layer/db ids
  json      run summary (tiles computed vs cached, timings)
  png       preview

Usage:
  python ml/terrain_seg/corridor.py --l2 ~/mars-data/test_orbital/l2_regional/jezero/v1 \\
      --start 77.45088 18.44463 --end 77.44229 18.43358 --width 300 --name landing_to_seitah
Python:
  from corridor import plan_route
  result = plan_route("s3://mars-sv-123412369922/orbital/l2_regional/jezero/v1",
                      (77.45088, 18.44463), (77.44229, 18.43358), width_m=300)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from affine import Affine
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import DATA, open_source  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TILE_M, CTX_M, ORTHO_RES, CELL = 256.0, 28.0, 0.25, 4.0
CLASSES = ["soil", "bedrock", "sand", "big_rock", "unknown"]
COST = {"soil": 1.0, "bedrock": 1.5, "sand": 3.0, "big_rock": 8.0, "unknown": 2.5}
COLOURS = np.array(
    [[215, 160, 90], [60, 120, 230], [250, 220, 40], [230, 40, 40], [90, 90, 90]], np.uint8
)
DEFAULT_DB = DATA / "rover/l9_terrain_db/jezero/v1"
DEFAULT_CACHE = DATA / "work/terrain_seg/tile_cache"
DEFAULT_OUT = DATA / "work/terrain_seg/routes"


class Database:
    def __init__(self, folder: Path):
        folder = Path(folder)
        z = np.load(folder / "vector_db.npz")
        self.emb = z["embedding"].astype(np.float32)
        self.cls = z["label"].astype(int)
        self.meta = json.loads((folder / "db.json").read_text())
        doc = json.loads((folder / "layer.json").read_text())
        digest = hashlib.sha1((folder / "vector_db.npz").read_bytes()).hexdigest()[:8]
        self.id = f"{doc['layer']}_{doc['site']}_{doc['version']}_{digest}"

    def classify(self, q: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        d = 1.0 - q @ self.emb.T
        scores = np.full((len(q), 4), np.inf)
        for c in range(4):
            cols = np.nonzero(self.cls == c)[0]
            if len(cols) >= k:
                scores[:, c] = np.sort(d[:, cols], axis=1)[:, :k].mean(1)
        cls = np.argmin(scores, 1)
        cls[scores.min(1) > self.meta["unknown_threshold_cosine"]] = 4
        return cls.astype(np.uint8), scores


def classify_tile(
    src, db: Database, i: int, j: int, cache: Path, device, k: int, stats: dict
) -> dict:
    f = cache / f"tile_{i}_{j}.npz"
    if f.exists():
        stats["cached"] += 1
        z = np.load(f)
        return {key: z[key] for key in z.files}
    from region_knn import momo_tokens

    x0, y0 = i * TILE_M, j * TILE_M
    rgb = src.read_rgb(x0 - CTX_M, y0 - CTX_M, x0 + TILE_M + CTX_M, y0 + TILE_M + CTX_M, ORTHO_RES)
    tokens, p = momo_tokens(rgb, device)
    g, c = int(TILE_M / ORTHO_RES) // p, int(CTX_M / ORTHO_RES) // p
    tok = tokens[c : c + g, c : c + g].reshape(-1, tokens.shape[2]).astype(np.float32)
    empty = (
        rgb[
            int(CTX_M / ORTHO_RES) : -int(CTX_M / ORTHO_RES),
            int(CTX_M / ORTHO_RES) : -int(CTX_M / ORTHO_RES),
        ].max()
        == 0
    )
    tok -= tok.mean(0, keepdims=True)
    tok /= np.maximum(np.linalg.norm(tok, axis=1, keepdims=True), 1e-9)
    cls, scores = db.classify(tok, k)
    if empty:
        cls[:] = 255
    ctx_px = int(CTX_M / ORTHO_RES)
    thumb = np.asarray(Image.fromarray(rgb[ctx_px:-ctx_px, ctx_px:-ctx_px]).resize((256, 256)))
    out = {
        "cls": cls.reshape(g, g),
        "score": scores.reshape(g, g, 4).astype(np.float16),
        "thumb": thumb,
    }
    np.savez_compressed(f, **out)
    stats["computed"] += 1
    return out


def corridor_tiles(a: np.ndarray, b: np.ndarray, width: float) -> list[tuple[int, int]]:
    n = max(2, int(np.linalg.norm(b - a) / (TILE_M / 4)))
    off = np.arange(-width / 2, width / 2 + 1, TILE_M / 4)
    tiles = set()
    for t in np.linspace(0, 1, n):
        x, y = a + t * (b - a)
        for dx in off:
            for dy in off:
                tiles.add((int(np.floor((x + dx) / TILE_M)), int(np.floor((y + dy) / TILE_M))))
    return sorted(tiles)


def plan_route(
    l2: str,
    start_lonlat,
    end_lonlat,
    width_m: float = 300.0,
    name: str = "route",
    db_dir: Path = DEFAULT_DB,
    cache_root: Path = DEFAULT_CACHE,
    out_dir: Path = DEFAULT_OUT,
    k: int | None = None,
    max_slope: float = 25.0,
) -> dict:
    t0 = time.time()
    src = open_source(l2)
    db = Database(db_dir)
    k = k or int(db.meta["k"])
    cache = Path(cache_root) / src.version / db.id
    cache.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    a = np.array(src.to_xy(*start_lonlat), float)
    b = np.array(src.to_xy(*end_lonlat), float)
    tiles = corridor_tiles(a, b, width_m)
    i0, j0 = min(t[0] for t in tiles), min(t[1] for t in tiles)
    i1, j1 = max(t[0] for t in tiles), max(t[1] for t in tiles)
    g = int(TILE_M / CELL)
    W, H = (i1 - i0 + 1) * g, (j1 - j0 + 1) * g
    cls = np.full((H, W), 255, np.uint8)
    thumb = np.zeros(((j1 - j0 + 1) * 256, (i1 - i0 + 1) * 256, 3), np.uint8)
    stats = {"computed": 0, "cached": 0}
    for i, j in tiles:
        t = classify_tile(src, db, i, j, cache, device, k, stats)
        r = (j1 - j) * g
        c = (i - i0) * g
        cls[r : r + g, c : c + g] = t["cls"]
        thumb[(j1 - j) * 256 : (j1 - j + 1) * 256, (i - i0) * 256 : (i - i0 + 1) * 256] = t["thumb"]
    t_class = time.time() - t0

    x0, y1 = i0 * TILE_M, (j1 + 1) * TILE_M
    dem = src.read_dem(x0, y1 - H * CELL, x0 + W * CELL, y1, CELL)[:H, :W]
    gy, gx = np.gradient(np.nan_to_num(dem, nan=np.nanmean(dem)), CELL)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    cost_tab = np.array([COST[c] for c in CLASSES] + [np.inf] * 251)
    cost = cost_tab[cls] * (1 + (slope / 10.0) ** 2)
    ys, xs = np.mgrid[0:H, 0:W]
    cx, cy = x0 + (xs + 0.5) * CELL, y1 - (ys + 0.5) * CELL
    d = b - a
    tp = np.clip(((cx - a[0]) * d[0] + (cy - a[1]) * d[1]) / (d @ d), 0, 1)
    dist = np.hypot(cx - (a[0] + tp * d[0]), cy - (a[1] + tp * d[1]))
    blocked = (slope > max_slope) | (dist > width_m / 2) | ~np.isfinite(cost) | np.isnan(dem)
    cost[blocked] = 1e6

    def rc(pt):
        return int((y1 - pt[1]) / CELL), int((pt[0] - x0) / CELL)

    from skimage.graph import route_through_array

    path, _ = route_through_array(cost, rc(a), rc(b), fully_connected=True, geometric=True)
    path = np.array(path)
    seg = np.hypot(np.diff(path[:, 0]), np.diff(path[:, 1])) * CELL
    on = cls[path[1:, 0], path[1:, 1]]
    length = float(seg.sum())
    share = {
        c: round(float(seg[on == n].sum() / max(length, 1e-9)), 4) for n, c in enumerate(CLASSES)
    }
    px, py = x0 + (path[:, 1] + 0.5) * CELL, y1 - (path[:, 0] + 0.5) * CELL
    lon, lat = src.to_lonlat(px, py)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    props = {
        "name": name,
        "length_m": round(length, 1),
        "straight_m": round(float(np.linalg.norm(d)), 1),
        "class_share": share,
        "l2": src.version,
        "database": db.id,
        "class_cost": COST,
        "max_slope_deg": max_slope,
    }
    gj = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": props,
                "geometry": {
                    "type": "LineString",
                    "coordinates": np.stack([lon, lat], 1).round(7).tolist(),
                },
            }
        ],
    }
    (out_dir / f"{name}.geojson").write_text(json.dumps(gj))
    result = {
        **props,
        "tiles": len(tiles),
        **stats,
        "classification_seconds": round(t_class, 1),
        "total_seconds": round(time.time() - t0, 1),
        "geojson": str(out_dir / f"{name}.geojson"),
    }
    if src.crs is not None:
        from common.cog import write_cog
        from common.validate import validate

        bands = np.stack([cls.astype(np.float32), np.where(blocked, -1, cost).astype(np.float32)])
        bands[0][cls == 255] = -1
        tif = out_dir / f"{name}.tif"
        write_cog(
            tif,
            bands,
            Affine(CELL, 0, x0, 0, -CELL, y1),
            src.crs,
            -1.0,
            {
                "MSV_KIND": "terrain_class",
                "BANDS": "class,cost",
                "CLASS_LEGEND": ",".join(f"{n}={c}" for n, c in enumerate(CLASSES)),
                "CLASS_COST": json.dumps(COST),
                "L2": src.version,
                "DATABASE": db.id,
            },
        )
        result["cog"] = str(tif)
        result["cog_valid"] = validate(str(tif)).ok
    (out_dir / f"{name}.json").write_text(json.dumps(result, indent=2))
    preview(thumb, cls, path, rc(a), rc(b), result, out_dir / f"{name}.png")
    return result


def preview(thumb, cls, path, a_rc, b_rc, res: dict, out_png: Path) -> None:
    lab = np.where(cls < 5, cls, 4)
    big = np.kron(COLOURS[lab], np.ones((4, 4, 1), np.uint8))[: thumb.shape[0], : thumb.shape[1]]
    over = (0.45 * thumb + 0.55 * big).astype(np.uint8)
    canvas = Image.new("RGB", (thumb.shape[1] * 2 + 30, thumb.shape[0] + 60), (24, 24, 24))
    canvas.paste(Image.fromarray(thumb), (10, 50))
    canvas.paste(Image.fromarray(over), (thumb.shape[1] + 20, 50))
    dr = ImageDraw.Draw(canvas)
    for x0 in (10, thumb.shape[1] + 20):
        dr.line(
            [(x0 + (c + 0.5) * 4, 50 + (r + 0.5) * 4) for r, c in path],
            fill=(255, 255, 255),
            width=3,
        )
        dr.line(
            [(x0 + a_rc[1] * 4, 50 + a_rc[0] * 4), (x0 + b_rc[1] * 4, 50 + b_rc[0] * 4)],
            fill=(255, 0, 255),
            width=1,
        )
    dr.text(
        (10, 14),
        f"{res['name']}: {res['length_m']:.0f} m (straight {res['straight_m']:.0f})"
        f" | L2 {res['l2']} | tiles {res['computed']} live / {res['cached']} cached",
        fill=(255, 255, 255),
    )
    canvas.save(out_png)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--l2", required=True, help="L2 layer folder or s3:// URI, or 'places'")
    ap.add_argument("--start", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    ap.add_argument("--end", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    ap.add_argument("--width", type=float, default=300.0)
    ap.add_argument("--name", default="route")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--max-slope", type=float, default=25.0)
    a = ap.parse_args()
    res = plan_route(
        a.l2,
        tuple(a.start),
        tuple(a.end),
        a.width,
        a.name,
        a.db,
        a.cache,
        a.out,
        max_slope=a.max_slope,
    )
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
