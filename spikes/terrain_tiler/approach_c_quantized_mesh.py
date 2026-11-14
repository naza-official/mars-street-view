import argparse
import gzip
import json
import struct
from pathlib import Path

import numpy as np
import rasterio
from grid import grid_triangles
from pydelatin import Delatin
from pyproj import CRS, Transformer
from quantized_mesh_encoder import Ellipsoid, encode
from rasterio.warp import transform_bounds
from scipy.ndimage import map_coordinates

from common.crs import MARS_RADIUS_M, geographic

MARS = Ellipsoid(MARS_RADIUS_M, MARS_RADIUS_M)
SAMPLES = 65


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    size = 180.0 / 2**z
    west, south = -180.0 + x * size, -90.0 + y * size
    return west, south, west + size, south + size


def tiles_inside(bounds: tuple[float, ...], z: int) -> list[tuple[int, int, int]]:
    size = 180.0 / 2**z
    x0, x1 = int(np.ceil((bounds[0] + 180) / size)), int(np.floor((bounds[2] + 180) / size))
    y0, y1 = int(np.ceil((bounds[1] + 90) / size)), int(np.floor((bounds[3] + 90) / size))
    return [(z, x, y) for x in range(x0, x1) for y in range(y0, y1)]


def tiles_intersecting(bounds: tuple[float, ...], z: int) -> list[tuple[int, int, int]]:
    size = 180.0 / 2**z
    x0, x1 = int((bounds[0] + 180) // size), int((bounds[2] + 180) // size)
    y0, y1 = int((bounds[1] + 90) // size), int((bounds[3] + 90) // size)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


class Dem:
    def __init__(self, path: Path) -> None:
        with rasterio.open(path) as src:
            self.height = src.read(1).astype(np.float64)
            self.transform = src.transform
            crs = CRS.from_wkt(src.crs.to_wkt())
            self.bounds = transform_bounds(src.crs, rasterio.crs.CRS.from_wkt(
                geographic().to_wkt()), *src.bounds)
        self.to_xy = Transformer.from_crs(geographic(), crs, always_xy=True)

    def sample(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        xs, ys = self.to_xy.transform(lon, lat)
        cols, rows = ~self.transform @ (xs, ys)
        return map_coordinates(self.height, [rows - 0.5, cols - 0.5], order=1, mode="nearest")


def tile_positions(dem: Dem, bounds: tuple[float, ...], max_error: float):
    west, south, east, north = bounds
    lon, lat = np.meshgrid(np.linspace(west, east, SAMPLES), np.linspace(north, south, SAMPLES))
    heights = dem.sample(lon, lat).astype(np.float32)
    if max_error < 0:
        positions = np.stack([lon.ravel(), lat.ravel(), heights.ravel()], axis=1)
        return positions, grid_triangles(SAMPLES, SAMPLES), (lon, lat, heights)
    tin = Delatin(heights, max_error=max_error)
    vx, vy, vh = tin.vertices[:, 0], tin.vertices[:, 1], tin.vertices[:, 2]
    lon_v = west + vx / (SAMPLES - 1) * (east - west)
    lat_v = south + vy / (SAMPLES - 1) * (north - south)
    positions = np.stack([lon_v, lat_v, vh], axis=1)
    return positions, tin.triangles.astype(np.uint32), (lon, lat, heights)


def zigzag_decode(values: np.ndarray) -> np.ndarray:
    v = values.astype(np.int32)
    return np.cumsum((v >> 1) ^ -(v & 1))


def decode_heights(data: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    min_h, max_h = struct.unpack_from("<ff", data, 24)
    count = struct.unpack_from("<I", data, 88)[0]
    raw = np.frombuffer(data, "<u2", count * 3, 92).reshape(3, count)
    u, v, h = (zigzag_decode(r) / 32767.0 for r in raw)
    return u, v, min_h + h * (max_h - min_h)


def write_tile(dem: Dem, out: Path, zxy: tuple[int, int, int], max_error: float) -> dict:
    bounds = tile_bounds(*zxy)
    positions, triangles, _ = tile_positions(dem, bounds, max_error)
    path = out / f"{zxy[0]}/{zxy[1]}/{zxy[2]}.terrain"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        encode(f, positions, triangles, bounds=bounds, ellipsoid=MARS)
    u, v, h = decode_heights(path.read_bytes())
    west, south, east, north = bounds
    truth = dem.sample(west + u * (east - west), south + v * (north - south))
    centre = np.linalg.norm(struct.unpack_from("<ddd", path.read_bytes(), 0)) - MARS_RADIUS_M
    return {"centre_height_m": float(centre), "h_range": [float(h.min()), float(h.max())],
            "bytes": path.stat().st_size, "gzip": len(gzip.compress(path.read_bytes())),
            "vertices": len(positions), "max_height_error_m": float(np.max(np.abs(h - truth)))}


def availability(bounds: tuple[float, ...], z: int, first_full: int) -> list[dict]:
    zxys = tiles_intersecting(bounds, z) if z < first_full else tiles_inside(bounds, z)
    xs, ys = [x for _, x, _ in zxys], [y for _, _, y in zxys]
    return [{"startX": min(xs), "endX": max(xs), "startY": min(ys), "endY": max(ys)}]


def build(dem_path: Path, out: Path, levels: list[int], max_error: float,
          parent_max_error: float) -> dict:
    dem = Dem(dem_path)
    per_level = {}
    for z in range(min(levels)):
        for zxy in tiles_intersecting(dem.bounds, z):
            write_tile(dem, out, zxy, parent_max_error)
    for z in levels:
        stats = [write_tile(dem, out, zxy, max_error) for zxy in tiles_inside(dem.bounds, z)]
        per_level[z] = {
            "tiles": len(stats),
            "bytes": sum(s["bytes"] for s in stats),
            "gzip_bytes": sum(s["gzip"] for s in stats),
            "mean_vertices": float(np.mean([s["vertices"] for s in stats])),
            "max_height_error_m": max(s["max_height_error_m"] for s in stats),
            "centre_inside_height_range": all(
                s["h_range"][0] - 50 <= s["centre_height_m"] <= s["h_range"][1] + 50
                for s in stats),
        }
    available = [availability(dem.bounds, z, min(levels)) for z in range(max(levels) + 1)]
    terrain = {"available": available, "tilejson": "2.1.0", "format": "quantized-mesh-1.0",
               "version": "1.0.0", "scheme": "tms", "tiles": ["{z}/{x}/{y}.terrain"],
               "projection": "EPSG:4326",
               "minzoom": 0, "maxzoom": max(levels), "bounds": list(dem.bounds)}
    (out / "layer.json").write_text(json.dumps(terrain, indent=1))
    return per_level


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dem", type=Path)
    parser.add_argument("--out", type=Path, default=Path("raw/spike/c_qmesh"))
    parser.add_argument("--levels", type=int, nargs="+", default=[6, 7, 8, 9])
    parser.add_argument("--max-error", type=float, default=5.0)
    parser.add_argument("--parent-max-error", type=float, default=5.0)
    args = parser.parse_args()
    summary = build(args.dem, args.out, args.levels, args.max_error, args.parent_max_error)
    (args.out / "stats.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
