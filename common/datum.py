import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

from common.crs import MARS_RADIUS_M, geographic

AREOID_RADIUS_OFFSET_M = 3396000.0
AREOID_LINES = 720
AREOID_SAMPLES = 1440
AREOID_PPD = 4.0


@dataclass(frozen=True)
class Areoid:
    offset_m: np.ndarray
    ppd: float = AREOID_PPD


def read_areoid(path: Path) -> Areoid:
    raw = np.fromfile(path, dtype=">i2").reshape(AREOID_LINES, AREOID_SAMPLES)
    return Areoid(offset_m=raw.astype(np.float64) + AREOID_RADIUS_OFFSET_M - MARS_RADIUS_M)


def areoid_offset(areoid: Areoid, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    grid = areoid.offset_m
    col = np.mod(np.asarray(lon, dtype=np.float64), 360.0) * areoid.ppd - 0.5
    row = np.clip((90.0 - np.asarray(lat, dtype=np.float64)) * areoid.ppd - 0.5,
                  0, grid.shape[0] - 1)
    c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
    fc, fr = col - c0, row - r0
    c0w, c1w = np.mod(c0, grid.shape[1]), np.mod(c0 + 1, grid.shape[1])
    r1 = np.minimum(r0 + 1, grid.shape[0] - 1)
    top = grid[r0, c0w] * (1 - fc) + grid[r0, c1w] * fc
    bottom = grid[r1, c0w] * (1 - fc) + grid[r1, c1w] * fc
    return top * (1 - fr) + bottom * fr


def areoid_to_sphere(heights: np.ndarray, lon: np.ndarray, lat: np.ndarray,
                     areoid: Areoid) -> np.ndarray:
    return heights + areoid_offset(areoid, lon, lat)


def dem_geoid_command(src: Path, out_prefix: Path) -> list[str]:
    return ["dem_geoid", "--geoid", "mola", "--reverse-adjustment", str(src), "-o", str(out_prefix)]


def dem_geoid_output(out_prefix: Path) -> Path:
    return Path(f"{out_prefix}-adj.tif")


def run_dem_geoid(src: Path, out_prefix: Path) -> Path:
    if shutil.which("dem_geoid") is None:
        raise RuntimeError("dem_geoid not on PATH; run inside the mars-sv image")
    subprocess.run(dem_geoid_command(src, out_prefix), check=True)
    return dem_geoid_output(out_prefix)


def sample_pixel_lonlat(src: rasterio.DatasetReader, n: int,
                        seed: int = 0) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    rows = rng.integers(0, src.height, n)
    cols = rng.integers(0, src.width, n)
    xs, ys = rasterio.transform.xy(src.transform, rows, cols)
    to_lonlat = Transformer.from_crs(src.crs, geographic(), always_xy=True)
    lon, lat = to_lonlat.transform(np.asarray(xs), np.asarray(ys))
    return rows, cols, lon, lat


def check_adjustment(original: Path, adjusted: Path, areoid: Areoid,
                     n: int = 500) -> dict[str, float]:
    with rasterio.open(original) as a, rasterio.open(adjusted) as b:
        rows, cols, lon, lat = sample_pixel_lonlat(a, n)
        before = a.read(1, masked=True)[rows, cols]
        after = b.read(1, masked=True)[rows, cols]
    valid = ~(np.ma.getmaskarray(before) | np.ma.getmaskarray(after))
    residual = (after - before)[valid] - areoid_offset(areoid, lon[valid], lat[valid])
    return {
        "n": int(valid.sum()),
        "mean_m": float(np.mean(residual)),
        "max_abs_m": float(np.max(np.abs(residual))),
    }
