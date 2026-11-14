import argparse
import hashlib
import json
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import boto3
import rasterio
import rasterio.shutil
from affine import Affine
from boto3.s3.transfer import TransferConfig

from common import datum
from common.crs import METRES_PER_DEGREE, global_cylindrical

SOURCE_BUCKET = "asc-pds-services"
PUBLIC_BASE = "https://planetarymaps.usgs.gov/mosaic/"
AREOID_URL = ("https://pds-geosciences.wustl.edu/mgs/mgs-m-mola-5-megdr-l3-v1/"
              "mgsl_300x/meg004/mega90n000cb.img")
AREOID_MD5 = "2b804ddd9524ce7cb882b35abce7ab41"
DTM_NODATA = -32768.0
ORTHO_NODATA = 0
MAX_AREOID_RESIDUAL_M = 2.0
LICENSE = "public domain (USGS Astrogeology)"


@dataclass(frozen=True)
class Source:
    name: str
    ppd: int

    @property
    def key(self) -> str:
        return f"mosaic/{self.name}"

    @property
    def s3_uri(self) -> str:
        return f"s3://{SOURCE_BUCKET}/{self.key}"

    @property
    def url(self) -> str:
        return PUBLIC_BASE + self.name


MOLA = Source("Mars_MGS_MOLA_DEM_mosaic_global_463m.tif", 128)
MDIM = Source("Mars_Viking_MDIM21_ClrMosaic_global_232m.tif", 256)
SOURCES = {"mola": MOLA, "mdim": MDIM}


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""):
            digest.update(chunk)
    return digest.hexdigest()


def published_md5(source: Source) -> str:
    body = boto3.client("s3").get_object(Bucket=SOURCE_BUCKET, Key=f"{source.key}.md5")["Body"]
    return body.read().decode().split()[0]


def fetch(source: Source, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    config = TransferConfig(multipart_chunksize=64 << 20, max_concurrency=16)
    boto3.client("s3").download_file(SOURCE_BUCKET, source.key, str(out), Config=config)
    expected, actual = published_md5(source), md5_file(out)
    if expected != actual:
        raise ValueError(f"{out}: md5 {actual} != published {expected}")
    return out


def fetch_areoid(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(AREOID_URL, out)
    if md5_file(out) != AREOID_MD5:
        raise ValueError(f"{out}: unexpected md5")
    return out


def canonical_transform(ppd: int) -> Affine:
    pixel = METRES_PER_DEGREE / ppd
    return Affine(pixel, 0.0, -180.0 * METRES_PER_DEGREE, 0.0, -pixel, 90.0 * METRES_PER_DEGREE)


def _simple_source(path: Path, band: int, src_col: int, dst_col: int, width: int,
                   height: int) -> str:
    return (f'<SimpleSource><SourceFilename relativeToVRT="0">{path}</SourceFilename>'
            f"<SourceBand>{band}</SourceBand>"
            f'<SrcRect xOff="{src_col}" yOff="0" xSize="{width}" ySize="{height}"/>'
            f'<DstRect xOff="{dst_col}" yOff="0" xSize="{width}" ySize="{height}"/>'
            "</SimpleSource>")


def _metadata(tags: dict[str, str]) -> str:
    items = "".join(f'<MDI key="{k}">{escape(v)}</MDI>' for k, v in tags.items())
    return f"<Metadata>{items}</Metadata>" if tags else ""


def roll_vrt(src: Path, ppd: int, out: Path, tags: dict[str, str] | None = None) -> Path:
    with rasterio.open(src) as ds:
        width, height, count = ds.width, ds.height, ds.count
        dtype = {"uint8": "Byte", "int16": "Int16", "float32": "Float32"}[ds.dtypes[0]]
        nodata = ds.nodata
    if width != 360 * ppd or height != 180 * ppd:
        raise ValueError(f"{src}: {width}x{height} is not a {ppd} ppd global grid")
    half = width // 2
    gt = ", ".join(repr(v) for v in canonical_transform(ppd).to_gdal())
    bands = []
    for band in range(1, count + 1):
        sources = (_simple_source(src.resolve(), band, half, 0, half, height)
                   + _simple_source(src.resolve(), band, 0, half, half, height))
        nd = f"<NoDataValue>{nodata}</NoDataValue>" if nodata is not None else ""
        bands.append(f'<VRTRasterBand dataType="{dtype}" band="{band}">{nd}{sources}'
                     "</VRTRasterBand>")
    out.write_text(f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">'
                   f"<SRS>{escape(global_cylindrical().to_wkt())}</SRS>"
                   f"<GeoTransform>{gt}</GeoTransform>{_metadata(tags or {})}"
                   f"{''.join(bands)}</VRTDataset>\n")
    return out


def write_tiled(src: Path, out: Path) -> Path:
    rasterio.shutil.copy(src, out, driver="GTiff", tiled=True, blockxsize=512, blockysize=512,
                         compress="DEFLATE", predictor=2, bigtiff="IF_SAFER",
                         num_threads="ALL_CPUS")
    return out


def dtm_tags(source: Source, fetched: str, check: dict) -> dict[str, str]:
    return {"MSV_KIND": "elevation", "UNITS": "m", "MSV_HEIGHT_REF": "sphere_3396190",
            "MSV_SOURCE": source.url, "MSV_FETCHED": fetched,
            "MSV_GRID": "PDS label grid, 128 ppd, edges on 0/360 and +-90; GeoTIFF origin ignored",
            "MSV_DATUM_CONVERSION": "ASP dem_geoid --geoid mola --reverse-adjustment",
            "MSV_DEM_GEOID_CHECK": json.dumps(check)}


def ortho_tags(source: Source, fetched: str) -> dict[str, str]:
    return {"MSV_KIND": "imagery", "MSV_SOURCE": source.url, "MSV_FETCHED": fetched,
            "MSV_GRID": "PDS label grid, 256 ppd, edges on 0/360 and +-90; longitude roll only"}


def reference_georef(path: Path, ppd: int, nodata: float) -> None:
    with rasterio.open(path, "r+") as ds:
        ds.crs = rasterio.crs.CRS.from_wkt(global_cylindrical().to_wkt())
        ds.transform = canonical_transform(ppd)
        ds.nodata = nodata


def tag_file(path: Path, tags: dict[str, str]) -> None:
    with rasterio.open(path, "r+") as ds:
        ds.update_tags(**tags)


def to_cog(src: Path, out: Path, **options: object) -> Path:
    rasterio.shutil.copy(src, out, driver="COG", blocksize=512, bigtiff="IF_SAFER",
                         overview_resampling="average", num_threads="ALL_CPUS", **options)
    return out


def make_dtm(mola: Path, areoid: Path, work: Path, out: Path, fetched: str,
             ppd: int = MOLA.ppd) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    vrt = roll_vrt(mola, ppd, work / "mola_rolled.vrt")
    rolled = write_tiled(vrt, work / "mola_rolled.tif")
    adjusted = datum.run_dem_geoid(rolled, work / "mola")
    reference_georef(adjusted, ppd, DTM_NODATA)
    check = datum.check_adjustment(rolled, adjusted, datum.read_areoid(areoid), n=2000)
    if check["max_abs_m"] > MAX_AREOID_RESIDUAL_M:
        raise ValueError(f"dem_geoid differs from the PDS areoid: {check}")
    tag_file(adjusted, dtm_tags(MOLA, fetched, check))
    to_cog(adjusted, out, compress="DEFLATE", predictor=3)
    return check


def make_ortho(mdim: Path, work: Path, out: Path, fetched: str, ppd: int = MDIM.ppd) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    vrt = roll_vrt(mdim, ppd, work / "mdim_rolled.vrt", ortho_tags(MDIM, fetched))
    return to_cog(vrt, out, compress="JPEG", quality=90)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m layers.l1_global.ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("source", choices=[*SOURCES, "areoid"])
    f.add_argument("out", type=Path)
    d = sub.add_parser("dtm")
    d.add_argument("mola", type=Path)
    d.add_argument("areoid", type=Path)
    d.add_argument("out", type=Path)
    d.add_argument("--work", type=Path, required=True)
    d.add_argument("--fetched", required=True)
    o = sub.add_parser("ortho")
    o.add_argument("mdim", type=Path)
    o.add_argument("out", type=Path)
    o.add_argument("--work", type=Path, required=True)
    o.add_argument("--fetched", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "fetch":
        areoid = args.source == "areoid"
        out = fetch_areoid(args.out) if areoid else fetch(SOURCES[args.source], args.out)
        print(f"fetched {out}")
    elif args.cmd == "dtm":
        print(json.dumps(make_dtm(args.mola, args.areoid, args.work, args.out, args.fetched)))
    else:
        print(f"wrote {make_ortho(args.mdim, args.work, args.out, args.fetched)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
