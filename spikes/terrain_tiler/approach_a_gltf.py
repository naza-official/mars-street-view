import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from glb import decode_positions, encode_glb
from grid import Grid, grid_triangles, load_grid

from common.crs import MARS_RADIUS_M

TILE_VERTICES = 65


@dataclass
class TileSpec:
    level: int
    row0: int
    col0: int
    span: int
    children: list["TileSpec"] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.level}/{self.row0}_{self.col0}"

    @property
    def stride(self) -> int:
        return max(1, self.span // (TILE_VERTICES - 1))


def build_quadtree(size: int, leaf_span: int, level: int = 0, row0: int = 0,
                   col0: int = 0, span: int | None = None) -> TileSpec:
    span = span or size
    tile = TileSpec(level, row0, col0, span)
    if span > leaf_span:
        half = span // 2
        tile.children = [build_quadtree(size, leaf_span, level + 1, row0 + dr, col0 + dc, half)
                         for dr in (0, half) for dc in (0, half)]
    return tile


def tile_indices(tile: TileSpec, size: int) -> tuple[np.ndarray, np.ndarray]:
    last = size - 1
    rows = np.unique(np.minimum(np.arange(tile.row0, tile.row0 + tile.span + 1, tile.stride), last))
    cols = np.unique(np.minimum(np.arange(tile.col0, tile.col0 + tile.span + 1, tile.stride), last))
    return rows, cols


def vertical_error(grid: Grid, tile: TileSpec, rows: np.ndarray, cols: np.ndarray) -> float:
    if tile.stride == 1:
        return 0.0
    full_r = np.arange(rows[0], rows[-1] + 1)
    full_c = np.arange(cols[0], cols[-1] + 1)
    coarse = grid.height[np.ix_(rows, cols)]
    along_c = np.array([np.interp(full_c, cols, line) for line in coarse])
    interp = np.array([np.interp(full_r, rows, line) for line in along_c.T]).T
    return float(np.max(np.abs(interp - grid.height[np.ix_(full_r, full_c)])))


def tile_mesh(grid: Grid, tile: TileSpec) -> dict:
    size = grid.height.shape[0]
    rows, cols = tile_indices(tile, size)
    xyz = grid.xyz[np.ix_(rows, cols)].reshape(-1, 3)
    origin = xyz.mean(axis=0)
    local = xyz - origin
    uv = np.stack(np.meshgrid(cols / (size - 1), rows / (size - 1)), axis=-1).reshape(-1, 2)
    return {
        "origin": origin,
        "local": local,
        "uv": uv,
        "indices": grid_triangles(len(rows), len(cols)),
        "heights": grid.height[np.ix_(rows, cols)].ravel(),
        "error": vertical_error(grid, tile, rows, cols),
    }


def translation(vector: np.ndarray) -> list[float]:
    m = np.eye(4)
    m[:3, 3] = vector
    return m.T.ravel().tolist()


def box(local: np.ndarray) -> list[float]:
    low, high = local.min(axis=0), local.max(axis=0)
    centre, half = (low + high) / 2, (high - low) / 2 + 1.0
    return [*centre, half[0], 0, 0, 0, half[1], 0, 0, 0, half[2]]


def write_tile(grid: Grid, tile: TileSpec, out: Path, parent_origin: np.ndarray,
               stats: dict) -> dict:
    mesh = tile_mesh(grid, tile)
    path = out / "tiles" / f"{tile.name}.glb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_glb(mesh["local"], mesh["uv"], mesh["indices"]))
    stats["tiles"].append({"name": tile.name, "bytes": path.stat().st_size,
                           "vertices": len(mesh["local"]), "error_m": mesh["error"]})
    check_heights(path, mesh, stats)
    children = [write_tile(grid, c, out, mesh["origin"], stats) for c in tile.children]
    node = {
        "transform": translation(mesh["origin"] - parent_origin),
        "boundingVolume": {"box": box(mesh["local"])},
        "geometricError": max([mesh["error"]] + [c["geometricError"] for c in children]),
        "refine": "REPLACE",
        "content": {"uri": f"tiles/{tile.name}.glb"},
    }
    if children:
        node["children"] = children
    return node


def check_heights(path: Path, mesh: dict, stats: dict) -> None:
    world = decode_positions(path.read_bytes()) + mesh["origin"]
    radial = np.linalg.norm(world, axis=1) - MARS_RADIUS_M
    stats["max_height_error_m"] = max(stats["max_height_error_m"],
                                      float(np.max(np.abs(radial - mesh["heights"]))))
    absolute32 = (mesh["local"] + mesh["origin"]).astype(np.float32).astype(np.float64)
    radial32 = np.linalg.norm(absolute32, axis=1) - MARS_RADIUS_M
    stats["float32_absolute_error_m"] = max(stats["float32_absolute_error_m"],
                                            float(np.max(np.abs(radial32 - mesh["heights"]))))


def build(dem: Path, out: Path, leaf_span: int) -> dict:
    grid = load_grid(dem)
    stats = {"tiles": [], "max_height_error_m": 0.0, "float32_absolute_error_m": 0.0}
    root = write_tile(grid, build_quadtree(grid.height.shape[0], leaf_span), out,
                      np.zeros(3), stats)
    tileset = {"asset": {"version": "1.1"}, "geometricError": root["geometricError"] * 2 + 1000,
               "root": root}
    (out / "tileset.json").write_text(json.dumps(tileset, indent=1))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dem", type=Path)
    parser.add_argument("--out", type=Path, default=Path("raw/spike/a_gltf"))
    parser.add_argument("--leaf-span", type=int, default=64)
    args = parser.parse_args()
    stats = build(args.dem, args.out, args.leaf_span)
    sizes = [t["bytes"] for t in stats["tiles"]]
    summary = {
        "tile_count": len(sizes),
        "total_bytes": sum(sizes) + (args.out / "tileset.json").stat().st_size,
        "tile_bytes_min_max": [min(sizes), max(sizes)],
        "max_height_error_m": stats["max_height_error_m"],
        "float32_absolute_error_m": stats["float32_absolute_error_m"],
        "geometric_error_by_level": {
            lvl: max(t["error_m"] for t in stats["tiles"] if t["name"].startswith(f"{lvl}/"))
            for lvl in sorted({t["name"].split("/")[0] for t in stats["tiles"]})},
    }
    (args.out / "stats.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
