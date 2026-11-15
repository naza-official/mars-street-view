import argparse
import dataclasses
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.windows import Window

from common import datum
from common.crs import METRES_PER_DEGREE, global_cylindrical
from common.terrain import Extent, TileKey
from layers.l1_global import ingest
from layers.l1_global.tiles import GLOBAL


def public_s3_url(source: ingest.Source) -> str:
    return f"https://{ingest.SOURCE_BUCKET}.s3.us-west-2.amazonaws.com/{source.key}"


def root_extent(key: TileKey) -> Extent:
    return GLOBAL.tile_extent(key)


def source_window(extent: Extent, ppd: int) -> Window:
    lon_w = extent.west if extent.west < 180 else extent.west - 360
    col = round((lon_w + 180.0) * ppd)
    row = round((90.0 - extent.north) * ppd)
    width = round((extent.east - extent.west) * ppd)
    height = round((extent.north - extent.south) * ppd)
    return Window(col, row, width, height)


def region_transform(extent: Extent, ppd: int) -> Affine:
    pixel = METRES_PER_DEGREE / ppd
    return Affine(pixel, 0.0, (extent.west - 180.0) * METRES_PER_DEGREE,
                  0.0, -pixel, extent.north * METRES_PER_DEGREE)


def clip(source: ingest.Source, extent: Extent, out: Path, nodata: float | None) -> Path:
    window = source_window(extent, source.ppd)
    with rasterio.open(f"/vsicurl/{public_s3_url(source)}") as src:
        data = src.read(window=window)
        profile = {"driver": "GTiff", "dtype": data.dtype, "count": data.shape[0],
                   "width": int(window.width), "height": int(window.height),
                   "crs": rasterio.crs.CRS.from_wkt(global_cylindrical().to_wkt()),
                   "transform": region_transform(extent, source.ppd), "nodata": nodata,
                   "tiled": True, "blockxsize": 256, "blockysize": 256, "compress": "DEFLATE"}
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data)
    return out


def make_dtm(extent: Extent, areoid: Path, work: Path, out: Path, fetched: str) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    raw = clip(ingest.MOLA, extent, work / "mola_clip.tif", ingest.DTM_NODATA)
    adjusted = datum.run_dem_geoid(raw, work / "mola")
    with rasterio.open(raw) as ref, rasterio.open(adjusted, "r+") as ds:
        ds.crs, ds.transform, ds.nodata = ref.crs, ref.transform, ingest.DTM_NODATA
    check = datum.check_adjustment(raw, adjusted, datum.read_areoid(areoid), n=2000)
    if check["max_abs_m"] > ingest.MAX_AREOID_RESIDUAL_M:
        raise ValueError(f"dem_geoid differs from the PDS areoid: {check}")
    ingest.tag_file(adjusted, ingest.dtm_tags(ingest.MOLA, fetched, check))
    ingest.to_cog(adjusted, out, compress="DEFLATE", predictor=3)
    return check


def make_ortho(extent: Extent, work: Path, out: Path, fetched: str) -> Path:
    raw = clip(ingest.MDIM, extent, work / "mdim_clip.tif", ingest.ORTHO_NODATA)
    ingest.tag_file(raw, ingest.ortho_tags(ingest.MDIM, fetched))
    return ingest.to_cog(raw, out, compress="JPEG", quality=90)


def sample(dtm: Path, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    with rasterio.open(dtm) as ds:
        xs = (np.mod(lon, 360.0) - 180.0) * METRES_PER_DEGREE
        ys = np.asarray(lat) * METRES_PER_DEGREE
        return np.array([v[0] for v in ds.sample(zip(xs, ys, strict=True))], dtype=np.float64)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m layers.l1_global.region")
    parser.add_argument("root", help="level/row/col of the subtree")
    parser.add_argument("out", type=Path, help="layer folder (dtm.tif, ortho.tif)")
    parser.add_argument("--areoid", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--fetched", required=True)
    args = parser.parse_args(argv)
    extent = root_extent(TileKey(*map(int, args.root.split("/"))))
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.areoid.exists():
        ingest.fetch_areoid(args.areoid)
    check = make_dtm(extent, args.areoid, args.work, args.out / "dtm.tif", args.fetched)
    make_ortho(extent, args.work, args.out / "ortho.tif", args.fetched)
    print(json.dumps({"extent": dataclasses.asdict(extent),
                      "dem_geoid_check": check}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
