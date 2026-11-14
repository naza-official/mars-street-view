import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.windows import Window, from_bounds

from common.crs import METRES_PER_DEGREE, geographic

AZIMUTHS_DEG = tuple(range(0, 360, 45))
SUN_ELEVATION_DEG = 30.0
MAX_SHIFT_PX = 24
WINDOW_PX = 384


@dataclass(frozen=True)
class Shift:
    east_m: float
    north_m: float
    magnitude_m: float
    score: float
    azimuth_deg: float | None
    pixel_m: float
    centre_lonlat: tuple[float, float]


def hillshade(height: np.ndarray, pixel_m: float, azimuth_deg: float,
              elevation_deg: float = SUN_ELEVATION_DEG) -> np.ndarray:
    dz_dy, dz_dx = np.gradient(height, pixel_m)
    slope = np.arctan(np.hypot(dz_dx, dz_dy))
    aspect = np.arctan2(-dz_dx, dz_dy)
    az, el = np.radians(azimuth_deg), np.radians(elevation_deg)
    return np.sin(el) * np.cos(slope) + np.cos(el) * np.sin(slope) * np.cos(az - aspect)


def _normalise(image: np.ndarray) -> np.ndarray:
    centred = image - image.mean()
    return centred / (centred.std() + 1e-12)


def ncc_surface(ref: np.ndarray, mov: np.ndarray, max_shift: int) -> np.ndarray:
    core = (slice(max_shift, ref.shape[0] - max_shift), slice(max_shift, ref.shape[1] - max_shift))
    a = _normalise(ref[core])
    size = 2 * max_shift + 1
    surface = np.empty((size, size))
    for i, dy in enumerate(range(-max_shift, max_shift + 1)):
        for j, dx in enumerate(range(-max_shift, max_shift + 1)):
            b = mov[core[0].start + dy:core[0].stop + dy, core[1].start + dx:core[1].stop + dx]
            surface[i, j] = float(np.mean(a * _normalise(b)))
    return surface


def _parabola(left: float, centre: float, right: float) -> float:
    denom = left - 2 * centre + right
    return 0.0 if denom == 0 else 0.5 * (left - right) / denom


def peak(surface: np.ndarray) -> tuple[float, float, float]:
    i, j = np.unravel_index(np.argmax(surface), surface.shape)
    sub_i = sub_j = 0.0
    if 0 < i < surface.shape[0] - 1:
        sub_i = _parabola(surface[i - 1, j], surface[i, j], surface[i + 1, j])
    if 0 < j < surface.shape[1] - 1:
        sub_j = _parabola(surface[i, j - 1], surface[i, j], surface[i, j + 1])
    centre = (surface.shape[0] - 1) / 2
    return float(i + sub_i - centre), float(j + sub_j - centre), float(surface[i, j])


def _bounds_around(src: rasterio.DatasetReader, lon: float, lat: float,
                   size_px: int) -> tuple[float, float, float, float]:
    to_xy = Transformer.from_crs(geographic(), CRS.from_wkt(src.crs.to_wkt()), always_xy=True)
    x, y = to_xy.transform(lon, lat)
    half = size_px * abs(src.res[0]) / 2
    return x - half, y - half, x + half, y + half


def read_pair(dtm: Path, ortho: Path, lon: float, lat: float,
              size_px: int) -> tuple[np.ndarray, np.ndarray, float]:
    with rasterio.open(dtm) as d, rasterio.open(ortho) as o:
        bounds = _bounds_around(d, lon, lat, size_px)
        height = d.read(1, window=from_bounds(*bounds, d.transform), out_dtype="float64")
        rgb = o.read(window=from_bounds(*bounds, o.transform), out_shape=(o.count, *height.shape),
                     resampling=Resampling.average, out_dtype="float64")
        pixel = abs(d.res[0])
    return height, rgb.mean(axis=0), pixel


def measure(dtm: Path, ortho: Path, lon: float, lat: float, size_px: int = WINDOW_PX,
            max_shift: int = MAX_SHIFT_PX) -> Shift:
    height, gray, pixel = read_pair(dtm, ortho, lon, lat, size_px + 2 * max_shift)
    best = max(((az, *peak(ncc_surface(hillshade(height, pixel, az), gray, max_shift)))
                for az in AZIMUTHS_DEG), key=lambda r: r[3])
    az, dy, dx, score = best
    east, north = dx * pixel, -dy * pixel
    return Shift(round(east, 1), round(north, 1), round(float(np.hypot(east, north)), 1),
                 round(score, 3), float(az), round(pixel, 3), (lon, lat))


def source_window(lon: float, lat: float, ppd: int, size_px: int) -> Window:
    col = round((np.mod(lon + 180.0, 360.0)) * ppd - size_px / 2)
    row = round((90.0 - lat) * ppd - size_px / 2)
    return Window(col, row, size_px, size_px)


def read_gray(path: Path, window: Window) -> np.ndarray:
    with rasterio.open(path) as ds:
        return ds.read(window=window, out_dtype="float64").mean(axis=0)


def conversion_shift(source: Path, product: Path, ppd: int, lon: float, lat: float,
                     size_px: int = 256, max_shift: int = 8) -> Shift:
    window = source_window(lon, lat, ppd, size_px + 2 * max_shift)
    before = read_gray(source, window)
    with rasterio.open(product) as ds:
        lon_west = np.mod(window.col_off / ppd - 180.0, 360.0)
        x0 = (lon_west - 180.0) * METRES_PER_DEGREE
        y0 = (90.0 - window.row_off / ppd) * METRES_PER_DEGREE
        pixel = abs(ds.res[0])
        bounds = (x0, y0 - window.height * pixel, x0 + window.width * pixel, y0)
        after = ds.read(window=from_bounds(*bounds, ds.transform),
                        out_shape=(ds.count, window.height, window.width),
                        out_dtype="float64").mean(axis=0)
    dy, dx, score = peak(ncc_surface(before, after, max_shift))
    east, north = dx * pixel, -dy * pixel
    return Shift(round(east, 1), round(north, 1), round(float(np.hypot(east, north)), 1),
                 round(score, 3), None, round(pixel, 3), (lon, lat))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m layers.l1_global.shift")
    sub = parser.add_subparsers(dest="cmd", required=True)
    reg = sub.add_parser("registration", help="ortho features vs DTM hillshade")
    reg.add_argument("dtm", type=Path)
    reg.add_argument("ortho", type=Path)
    conv = sub.add_parser("conversion", help="source image vs converted product")
    conv.add_argument("source", type=Path)
    conv.add_argument("product", type=Path)
    conv.add_argument("--ppd", type=int, required=True)
    for p in (reg, conv):
        p.add_argument("--lonlat", type=float, nargs=2, default=(77.5, 18.4))
        p.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "registration":
        found = measure(args.dtm, args.ortho, *args.lonlat)
    else:
        found = conversion_shift(args.source, args.product, args.ppd, *args.lonlat)
    result = json.dumps(asdict(found), indent=2)
    if args.out:
        args.out.write_text(result + "\n")
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
