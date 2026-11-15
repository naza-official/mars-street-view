import argparse
import io
import json
import multiprocessing as mp
import os
import posixpath
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from common import terrain
from common.crs import METRES_PER_DEGREE
from common.terrain import Extent, TileGrid, TileKey

LEAF_LEVEL = 7
SEGMENTS = 180
SPLIT_LEVEL = 4
DELATIN_FROM_LEVEL = 4
LEAF_MAX_ERROR_M = 10.0
TEXTURE_PX = 360
JPEG_QUALITY = 85
GLOBAL = TileGrid(Extent(0.0, -90.0, 360.0, 90.0), rows0=1, cols0=2)


@dataclass
class Job:
    grid: TileGrid
    root: TileKey | None
    corners: dict[int, np.ndarray]
    out: Path
    ortho: Path | None
    leaf_error_m: float = LEAF_MAX_ERROR_M
    depth: dict[TileKey, float] = field(default_factory=dict)


JOB: Job | None = None
ORTHO: rasterio.DatasetReader | None = None


def max_error_m(level: int, leaf_error_m: float) -> float:
    return leaf_error_m * 2 ** (LEAF_LEVEL - level)


def segment_deg(grid: TileGrid, level: int) -> float:
    rows, _ = grid.shape(level)
    return (grid.extent.north - grid.extent.south) / rows / SEGMENTS


def block_mean(pixels: np.ndarray, factor: int) -> np.ndarray:
    h, w = pixels.shape[0] // factor, pixels.shape[1] // factor
    blocks = pixels[:h * factor, :w * factor].reshape(h, factor, w, factor)
    return blocks.mean(axis=(1, 3), dtype=np.float64).astype(np.float32)


def corner_grid(pixels: np.ndarray, wrap_lon: bool, poles: bool) -> np.ndarray:
    padded = np.pad(np.pad(pixels, ((1, 1), (0, 0)), mode="edge"), ((0, 0), (1, 1)),
                    mode="wrap" if wrap_lon else "edge")
    corners = padded[:-1, :-1].astype(np.float32)
    corners += padded[1:, :-1]
    corners += padded[:-1, 1:]
    corners += padded[1:, 1:]
    corners *= 0.25
    if poles:
        corners[0] = pixels[0].mean(dtype=np.float64)
        corners[-1] = pixels[-1].mean(dtype=np.float64)
    return corners


def build_corners(pixels: np.ndarray, levels: range, is_global: bool) -> dict[int, np.ndarray]:
    corners, current = {}, pixels
    for level in sorted(levels, reverse=True):
        if level < LEAF_LEVEL:
            current = block_mean(current, 2)
        corners[level] = corner_grid(current, wrap_lon=is_global, poles=is_global)
    return corners


def region_offset(root: TileKey | None, level: int) -> tuple[int, int]:
    if root is None:
        return 0, 0
    shift = level - root.level
    return root.row << shift, root.col << shift


def tile_corners(job: Job, key: TileKey) -> np.ndarray:
    r0, c0 = region_offset(job.root, key.level)
    r, c = (key.row - r0) * SEGMENTS, (key.col - c0) * SEGMENTS
    return job.corners[key.level][r:r + SEGMENTS + 1, c:c + SEGMENTS + 1]


def child_corners(job: Job, key: TileKey) -> np.ndarray:
    r0, c0 = region_offset(job.root, key.level + 1)
    r, c = (2 * key.row - r0) * SEGMENTS, (2 * key.col - c0) * SEGMENTS
    return job.corners[key.level + 1][r:r + 2 * SEGMENTS + 1, c:c + 2 * SEGMENTS + 1]


def tile_keys(grid: TileGrid, root: TileKey | None) -> list[TileKey]:
    if root is None:
        return [k for level in range(LEAF_LEVEL + 1) for k in grid.keys(level)]
    keys, frontier = [], [root]
    while frontier:
        keys += frontier
        frontier = [c for k in frontier if k.level < LEAF_LEVEL for c in k.children()]
    return keys


def refinement(key: TileKey) -> tuple[TileKey, float]:
    assert JOB is not None
    if key.level == LEAF_LEVEL:
        return key, 0.0
    return key, terrain.refinement_error(tile_corners(JOB, key), child_corners(JOB, key))


def chain_errors(keys: list[TileKey], refine: dict[TileKey, float]) -> dict[TileKey, float]:
    chain: dict[TileKey, float] = {}
    for key in sorted(keys, key=lambda k: -k.level):
        below = [chain[c] for c in key.children() if c in chain] if key.level < LEAF_LEVEL else []
        chain[key] = refine[key] + max(below, default=0.0)
    return chain


def geometric_errors(grid: TileGrid, chain: dict[TileKey, float],
                     leaf_error_m: float) -> dict[TileKey, float]:
    out = {}
    for key, value in chain.items():
        if key.level == LEAF_LEVEL:
            out[key] = 0.0
            continue
        own = max_error_m(key.level, leaf_error_m) if key.level >= DELATIN_FROM_LEVEL else \
            terrain.sagitta_m(segment_deg(grid, key.level))
        out[key] = own + value + leaf_error_m
    return out


def skirt_depths(keys: list[TileKey], geometric: dict[TileKey, float],
                 leaf_error_m: float) -> dict[TileKey, float]:
    depth = {}
    for key in keys:
        parent = key.ancestor(key.level - 1) if key.level > 0 else key
        depth[key] = max(geometric.get(parent, geometric[key]), 4 * leaf_error_m) + 10.0
    return depth


def texture(extent: Extent) -> bytes | None:
    global ORTHO
    assert JOB is not None
    if JOB.ortho is None:
        return None
    if ORTHO is None:
        ORTHO = rasterio.open(JOB.ortho)
    bounds = ((extent.west - 180.0) * METRES_PER_DEGREE, extent.south * METRES_PER_DEGREE,
              (extent.east - 180.0) * METRES_PER_DEGREE, extent.north * METRES_PER_DEGREE)
    rgb = ORTHO.read(window=from_bounds(*bounds, ORTHO.transform),
                     out_shape=(3, TEXTURE_PX, TEXTURE_PX), resampling=Resampling.average)
    buf = io.BytesIO()
    Image.fromarray(np.moveaxis(rgb, 0, -1)).save(buf, "JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def tile_mesh(job: Job, key: TileKey, corners: np.ndarray, extent: Extent) -> terrain.Mesh:
    if key.level >= DELATIN_FROM_LEVEL:
        heights = terrain.curved_heights(corners, extent)
        mesh = terrain.simplified_mesh(heights, max_error_m(key.level, job.leaf_error_m))
    else:
        mesh = terrain.regular_mesh(SEGMENTS + 1)
    return terrain.with_skirts(mesh)


def tile_path(out: Path, key: TileKey) -> Path:
    return out / "tiles" / f"{key.name}.glb"


def build_tile(key: TileKey) -> dict:
    assert JOB is not None
    corners = tile_corners(JOB, key)
    extent = JOB.grid.tile_extent(key)
    mesh = tile_mesh(JOB, key, corners, extent)
    xyz = terrain.mesh_xyz(mesh, corners, extent, JOB.depth[key])
    origin = xyz[~mesh.skirt].mean(axis=0)
    local = xyz - origin
    glb = terrain.encode_glb(local, terrain.mesh_uv(mesh, SEGMENTS + 1), mesh.triangles,
                             texture(extent))
    path = tile_path(JOB.out, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(glb)
    quant = terrain.quantize(terrain.z_up_to_y_up(local)).max_error_m
    return {"key": [key.level, key.row, key.col], "origin": origin.tolist(),
            "box": terrain.obb(local, terrain.up_vector(*extent.centre)),
            "vertices": int((~mesh.skirt).sum()), "triangles": int(len(mesh.triangles)),
            "bytes": len(glb), "quant_m": quant}


def _init(job: Job) -> None:
    global JOB
    JOB = job


def run_pool(func, keys: list[TileKey], workers: int) -> list:
    if workers <= 1:
        return [func(k) for k in keys]
    with mp.get_context("fork").Pool(workers) as pool:
        return list(pool.imap_unordered(func, keys, chunksize=16))


def tile_uri(key: TileKey) -> str:
    return f"tiles/{key.name}.glb"


def subtree_uri(key: TileKey) -> str:
    return f"sub/{key.name}.json"


def relative(uri: str, base: str) -> str:
    return posixpath.relpath(uri, base) if base else uri


def tile_node(key: TileKey, records: dict, geometric: dict, parent_origin: np.ndarray,
              externals: dict[str, dict], base: str = "") -> dict:
    rec = records[key]
    origin = np.array(rec["origin"])
    children = []
    for child in (key.children() if key.level < LEAF_LEVEL else []):
        if child in records:
            children.append(child_entry(child, records, geometric, origin, externals, base))
    node = {"transform": terrain.translation(origin - parent_origin),
            "boundingVolume": {"box": rec["box"]},
            "geometricError": round(geometric[key] + rec["quant_m"], 3),
            "refine": "REPLACE", "content": {"uri": relative(tile_uri(key), base)}}
    if children:
        node["children"] = children
    return node


def child_entry(key: TileKey, records: dict, geometric: dict, parent_origin: np.ndarray,
                externals: dict[str, dict], base: str = "") -> dict:
    if key.level != SPLIT_LEVEL:
        return tile_node(key, records, geometric, parent_origin, externals, base)
    uri = subtree_uri(key)
    node = tile_node(key, records, geometric, parent_origin, externals, posixpath.dirname(uri))
    externals[uri] = {"asset": {"version": "1.1"}, "geometricError": node["geometricError"],
                      "root": node}
    offset = np.array(records[key]["origin"]) - parent_origin
    return {"boundingVolume": {"box": terrain.shift_box(records[key]["box"], offset)},
            "geometricError": node["geometricError"], "refine": "REPLACE",
            "content": {"uri": relative(uri, base)}}


def top_keys(records: dict) -> list[TileKey]:
    level = min(k.level for k in records)
    return sorted(k for k in records if k.level == level)


def assemble(records: dict, geometric: dict) -> tuple[dict, dict[str, dict]]:
    externals: dict[str, dict] = {}
    roots = [child_entry(k, records, geometric, np.zeros(3), externals) for k in top_keys(records)]
    top = max(r["geometricError"] for r in roots)
    radius = terrain.MARS_RADIUS_M + 30000.0
    root = {"boundingVolume": {"box": [0, 0, 0, radius, 0, 0, 0, radius, 0, 0, 0, radius]},
            "geometricError": round(2 * top, 3), "refine": "REPLACE", "children": roots}
    tileset = {"asset": {"version": "1.1", "generator": "mars-sv layers.l1_global.tiles"},
               "geometricError": round(4 * top, 3), "root": root}
    return tileset, externals


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, separators=(",", ":")))


def summary(records: dict, geometric: dict, seconds: float) -> dict:
    by_level: dict[int, dict] = {}
    for key, rec in records.items():
        s = by_level.setdefault(key.level, {"tiles": 0, "bytes": 0, "vertices": 0,
                                            "geometric_error_max_m": 0.0})
        s["tiles"] += 1
        s["bytes"] += rec["bytes"]
        s["vertices"] += rec["vertices"]
        s["geometric_error_max_m"] = max(s["geometric_error_max_m"], round(geometric[key], 2))
    return {"tiles": len(records), "bytes": sum(r["bytes"] for r in records.values()),
            "quant_max_m": round(max(r["quant_m"] for r in records.values()), 3),
            "seconds": round(seconds, 1), "levels": dict(sorted(by_level.items()))}


def build(dtm: Path, out: Path, ortho: Path | None, root: TileKey | None, workers: int,
          leaf_error_m: float = LEAF_MAX_ERROR_M) -> dict:
    start = time.time()
    with rasterio.open(dtm) as src:
        pixels = src.read(1).astype(np.float32)
    levels = range(0 if root is None else root.level, LEAF_LEVEL + 1)
    job = Job(GLOBAL, root, build_corners(pixels, levels, root is None), out, ortho, leaf_error_m)
    del pixels
    keys = tile_keys(GLOBAL, root)
    _init(job)
    refine = dict(run_pool(refinement, keys, workers))
    geometric = geometric_errors(GLOBAL, chain_errors(keys, refine), leaf_error_m)
    job.depth = skirt_depths(keys, geometric, leaf_error_m)
    records = {TileKey(*r["key"]): r for r in run_pool(build_tile, keys, workers)}
    tileset, externals = assemble(records, geometric)
    for uri, doc in externals.items():
        write_json(out / uri, doc)
    write_json(out / "tileset.json", tileset)
    stats = {**summary(records, geometric, time.time() - start), "leaf_error_m": leaf_error_m}
    (out.parent / f"{out.name}.stats.json").write_text(json.dumps(stats, indent=1) + "\n")
    return stats


def configure(leaf_level: int, texture_px: int, split_level: int) -> None:
    global LEAF_LEVEL, TEXTURE_PX, SPLIT_LEVEL
    LEAF_LEVEL, TEXTURE_PX, SPLIT_LEVEL = leaf_level, texture_px, split_level


def parse_key(text: str | None) -> TileKey | None:
    return None if text is None else TileKey(*map(int, text.split("/")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m layers.l1_global.tiles")
    parser.add_argument("dtm", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--ortho", type=Path)
    parser.add_argument("--root", help="level/row/col of a subtree (dtm must cover it exactly)")
    parser.add_argument("--leaf-error", type=float, default=LEAF_MAX_ERROR_M)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--leaf-level", type=int, default=LEAF_LEVEL)
    parser.add_argument("--texture-px", type=int, default=TEXTURE_PX)
    parser.add_argument("--split-level", type=int, default=SPLIT_LEVEL)
    args = parser.parse_args(argv)
    configure(args.leaf_level, args.texture_px, args.split_level)
    stats = build(args.dtm, args.out, args.ortho, parse_key(args.root), args.workers,
                  args.leaf_error)
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
