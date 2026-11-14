from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Transformer

from common.crs import geographic, lonlat_to_xyz


@dataclass(frozen=True)
class Grid:
    lon: np.ndarray
    lat: np.ndarray
    height: np.ndarray
    xyz: np.ndarray
    pixel_m: float


def load_grid(path: Path) -> Grid:
    with rasterio.open(path) as src:
        height = src.read(1).astype(np.float64)
        rows, cols = np.mgrid[0:src.height, 0:src.width] + 0.5
        xs, ys = src.transform @ (cols, rows)
        to_lonlat = Transformer.from_crs(CRS.from_wkt(src.crs.to_wkt()), geographic(),
                                         always_xy=True)
        lon, lat = to_lonlat.transform(xs, ys)
        pixel = abs(src.res[0])
    return Grid(lon, lat, height, lonlat_to_xyz(lon, lat, height), pixel)


def grid_triangles(rows: int, cols: int) -> np.ndarray:
    r, c = np.mgrid[0:rows - 1, 0:cols - 1]
    a = (r * cols + c).ravel()
    b, d = a + 1, a + cols
    e = d + 1
    return np.stack([a, d, b, b, d, e], axis=1).reshape(-1, 3).astype(np.uint32)
