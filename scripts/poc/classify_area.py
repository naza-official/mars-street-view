import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import torch
from affine import Affine

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml" / "terrain_seg"))

from corridor import CELL, CTX_M, TILE_M, Database, classify_tile  # noqa: E402
from sources import open_source  # noqa: E402


def inner_tiles(bounds: list[float]) -> tuple[range, range]:
    west, south, east, north = bounds
    cols = range(math.ceil((west + CTX_M) / TILE_M), math.floor((east - CTX_M) / TILE_M))
    rows = range(math.ceil((south + CTX_M) / TILE_M), math.floor((north - CTX_M) / TILE_M))
    return cols, rows


def pick_device(name: str) -> torch.device:
    if name == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    return torch.device(name)


def write_cog(path: Path, cls: np.ndarray, crs, x0: float, y1: float) -> None:
    profile = {"driver": "GTiff", "width": cls.shape[1], "height": cls.shape[0], "count": 1,
               "dtype": "uint8", "crs": crs, "nodata": 255,
               "transform": Affine(CELL, 0, x0, 0, -CELL, y1), "tiled": True,
               "blockxsize": 256, "blockysize": 256, "compress": "DEFLATE"}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(cls, 1)
        dst.update_tags(CLASS_LEGEND=json.dumps(
            {"0": "soil", "1": "bedrock", "2": "sand", "3": "big_rock", "4": "unknown"}))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--l2", required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--cache-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    src, db = open_source(a.l2), Database(a.db)
    cache = a.cache_root / src.version / db.id
    cache.mkdir(parents=True, exist_ok=True)
    with rasterio.open(Path(a.l2) / "ortho.tif") as o:
        bounds, crs = list(o.bounds), o.crs
    cols, rows = inner_tiles(bounds)
    g = int(TILE_M / CELL)
    cls = np.full((len(rows) * g, len(cols) * g), 255, np.uint8)
    device, stats, t0 = pick_device(a.device), {"computed": 0, "cached": 0}, time.time()
    tiles = [(i, j) for j in rows for i in cols][: a.limit or None]
    for n, (i, j) in enumerate(tiles, 1):
        t = classify_tile(src, db, i, j, cache, device, int(db.meta["k"]), stats)
        r, c = (rows[-1] - j) * g, (i - cols[0]) * g
        cls[r:r + g, c:c + g] = t["cls"]
        print(f"[{n}/{len(tiles)}] tile {i},{j} {time.time() - t0:.0f}s {stats}", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    write_cog(a.out, cls, crs, cols[0] * TILE_M, (rows[-1] + 1) * TILE_M)
    valid = cls[cls != 255]
    share = {n: round(float((valid == k).mean()), 4)
             for k, n in enumerate(["soil", "bedrock", "sand", "big_rock", "unknown"])}
    print(json.dumps({"tiles": len(tiles), **stats, "device": str(device), "share": share,
                      "seconds": round(time.time() - t0, 1), "out": str(a.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
