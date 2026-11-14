import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.windows import Window

from common.cog import write_cog
from common.crs import MARS_RADIUS_M, global_cylindrical
from common.datum import areoid_to_sphere, read_areoid

MOLA_URL = "https://planetarymaps.usgs.gov/mosaic/Mars_MGS_MOLA_DEM_mosaic_global_463m.tif"
MDIM_URL = "https://planetarymaps.usgs.gov/mosaic/Mars_Viking_MDIM21_ClrMosaic_global_232m.tif"
AREOID_PATH = Path("raw/areoid/mega90n000cb.img")
JEZERO_CENTRE = (77.5, 18.4)
GDAL_HTTP_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "3"}


@dataclass(frozen=True)
class Source:
    url: str
    ppd: int


MOLA = Source(MOLA_URL, 128)
MDIM = Source(MDIM_URL, 256)


def pixel_size_m(ppd: int) -> float:
    return 2 * np.pi * MARS_RADIUS_M / (360 * ppd)


def centred_window(source: Source, lon: float, lat: float, size: int) -> Window:
    col = round((lon + 180.0) * source.ppd - size / 2)
    row = round((90.0 - lat) * source.ppd - size / 2)
    return Window(col, row, size, size)


def window_transform(source: Source, window: Window) -> Affine:
    pixel = pixel_size_m(source.ppd)
    lon_west = window.col_off / source.ppd - 180.0
    lat_north = 90.0 - window.row_off / source.ppd
    x_west = np.radians(lon_west - 180.0) * MARS_RADIUS_M
    y_north = np.radians(lat_north) * MARS_RADIUS_M
    return Affine(pixel, 0.0, x_west, 0.0, -pixel, y_north)


def window_lonlat(source: Source, window: Window) -> tuple[np.ndarray, np.ndarray]:
    cols = window.col_off + np.arange(window.width) + 0.5
    rows = window.row_off + np.arange(window.height) + 0.5
    lon, lat = np.meshgrid(cols / source.ppd - 180.0, 90.0 - rows / source.ppd)
    return lon, lat


def read_window(source: Source, window: Window) -> np.ndarray:
    with rasterio.Env(**GDAL_HTTP_ENV), rasterio.open(source.url) as src:
        return src.read(window=window)


def source_tags(source: Source, kind: str, extra: dict[str, str]) -> dict[str, str]:
    return {"MSV_KIND": kind, "MSV_SOURCE": source.url,
            "MSV_FETCHED": date.today().isoformat(), **extra}


def make_mola(out: Path, size: int, areoid_path: Path) -> Path:
    window = centred_window(MOLA, *JEZERO_CENTRE, size)
    raw = read_window(MOLA, window)[0]
    lon, lat = window_lonlat(MOLA, window)
    nodata = -32768.0
    valid = raw != -32768
    heights = areoid_to_sphere(raw.astype(np.float64), lon, lat, read_areoid(areoid_path))
    data = np.where(valid, heights, nodata).astype(np.float32)
    tags = source_tags(MOLA, "elevation", {
        "UNITS": "m",
        "MSV_HEIGHT_REF": "sphere_3396190",
        "MSV_DATUM_CONVERSION": "common.datum.areoid_to_sphere (PDS mega90n000cb, bilinear)",
        "MSV_GRID": "exact 128 ppd; source GeoTIFF pixel size ignored",
    })
    return write_cog(out, data, window_transform(MOLA, window), global_cylindrical(), nodata, tags)


def make_mdim(out: Path, size: int) -> Path:
    window = centred_window(MDIM, *JEZERO_CENTRE, size)
    data = read_window(MDIM, window).astype(np.uint8)
    tags = source_tags(MDIM, "imagery", {"MSV_GRID": "exact 256 ppd"})
    return write_cog(out, data, window_transform(MDIM, window), global_cylindrical(), 0, tags)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures"))
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--areoid", type=Path, default=AREOID_PATH)
    parser.add_argument("--only", choices=["mola", "mdim"])
    args = parser.parse_args()
    suffix = "" if args.size == 256 else f"_{args.size}"
    if args.only in (None, "mola"):
        print(make_mola(args.out / f"mola_jezero{suffix}.tif", args.size, args.areoid))
    if args.only in (None, "mdim"):
        print(make_mdim(args.out / f"mdim_jezero{suffix}.tif", args.size))


if __name__ == "__main__":
    main()
