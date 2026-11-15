import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import Window

from common import datum, layerjson
from common.crs import BASE_CRS_CODE, DATUM_ID, geographic, site_crs
from common.sites import load_site
from layers.l1_global import ingest
from layers.l2_regional.poc import (
    BLOCK_ROWS,
    DTM_NODATA,
    LICENSE,
    SOURCE_BASE,
    merged_block,
    valid,
    warped,
)

DTM_RES_M = 1.0
ORTHO_RES_M = 0.25
SOURCES = {
    "dtm": ("HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0"
            "_blend40.tif",
            "CTX/JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0.tif"),
    "ortho": ("HiRISE/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif",
              "CTX/JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0.tif"),
}


def site_bounds(crs, bbox: list[float]) -> tuple[float, float, float, float]:
    to_site = Transformer.from_crs(geographic(), crs, always_xy=True)
    lons = np.linspace(bbox[0], bbox[2], 21)
    lats = np.linspace(bbox[1], bbox[3], 21)
    lon, lat = np.meshgrid(lons, lats)
    x, y = to_site.transform(lon.ravel(), lat.ravel())
    snap = 4.0
    return (math.floor(min(x) / snap) * snap, math.floor(min(y) / snap) * snap,
            math.ceil(max(x) / snap) * snap, math.ceil(max(y) / snap) * snap)


def lonlat_bounds(to_geo: Transformer, bounds: tuple[float, ...]) -> list[float]:
    xs = np.linspace(bounds[0], bounds[2], 41)
    ys = np.linspace(bounds[1], bounds[3], 41)
    x, y = np.meshgrid(xs, ys)
    lon, lat = to_geo.transform(x.ravel(), y.ravel())
    return [math.floor(min(lon) * 1e4) / 1e4, math.floor(min(lat) * 1e4) / 1e4,
            math.ceil(max(lon) * 1e4) / 1e4, math.ceil(max(lat) * 1e4) / 1e4]


def profile(crs, bounds: tuple[float, ...], res: float, count: int, dtype: str,
            nodata: float | None) -> dict:
    west, south, east, north = bounds
    return {"driver": "GTiff", "width": round((east - west) / res),
            "height": round((north - south) / res), "count": count, "dtype": dtype,
            "crs": rasterio.crs.CRS.from_wkt(crs.to_wkt()),
            "transform": Affine(res, 0.0, west, 0.0, -res, north), "nodata": nodata,
            "tiled": True, "blockxsize": 512, "blockysize": 512, "bigtiff": "IF_SAFER"}


def block_lonlat(prof: dict, to_geo: Transformer, row0: int, rows: int):
    t = prof["transform"]
    xs = t.c + (np.arange(prof["width"]) + 0.5) * t.a
    ys = t.f + (np.arange(row0, row0 + rows) + 0.5) * t.e
    x, y = np.meshgrid(xs, ys)
    return to_geo.transform(x, y)


def make_dtm(raw: list[Path], areoid: Path, prof: dict, out: Path, to_geo: Transformer) -> dict:
    model = datum.read_areoid(areoid)
    vrts = [warped(p, prof, Resampling.bilinear) for p in raw]
    fine_px = 0
    with rasterio.open(out, "w", **prof) as dst:
        for row0 in range(0, prof["height"], BLOCK_ROWS):
            window = Window(0, row0, prof["width"], min(BLOCK_ROWS, prof["height"] - row0))
            fine_px += int(valid(vrts[0].read(1, window=window), vrts[0].nodata).sum())
            heights, ok = merged_block(vrts, window)
            lon, lat = block_lonlat(prof, to_geo, row0, window.height)
            heights[ok] += datum.areoid_offset(model, lon[ok], lat[ok])
            heights[~ok] = DTM_NODATA
            dst.write(heights.astype(np.float32), 1, window=window)
    return {"pixels": prof["width"] * prof["height"], "hirise_px": fine_px}


def make_ortho(raw: list[Path], prof: dict, out: Path) -> None:
    vrts = [warped(p, prof, Resampling.average) for p in raw]
    with rasterio.open(out, "w", **prof) as dst:
        for row0 in range(0, prof["height"], BLOCK_ROWS):
            window = Window(0, row0, prof["width"], min(BLOCK_ROWS, prof["height"] - row0))
            grey, ok = merged_block(vrts, window)
            dst.write(np.where(ok, np.clip(grey, 1, 255), 0).astype(np.uint8), 1, window=window)


def finish_cog(path: Path, **options: object) -> None:
    tmp = path.with_suffix(".cog.tif")
    ingest.to_cog(path, tmp, **options)
    tmp.replace(path)


def layer_doc(site: str, bbox: list[float], stats: dict) -> dict:
    share = stats["hirise_px"] / stats["pixels"]
    return {
        "layer": "l2_regional", "site": site, "version": "v1",
        "schema": layerjson.SCHEMA_VERSION, "owner": "A", "crs": BASE_CRS_CODE,
        "datum": DATUM_ID, "bounds_lonlat": bbox, "resolution_m": ORTHO_RES_M,
        "assets": {"dtm": "dtm.tif", "ortho": "ortho.tif"}, "depends_on": [],
        "validated": True,
        "sources": [{"url": SOURCE_BASE + s, "fetched": "2026-11-15", "license": LICENSE}
                    for pair in SOURCES.values() for s in pair],
        "built_with": "local PoC (layers/l2_regional/site_layer.py)",
        "notes": (f"Local PoC in the contract format for B's route service: site CRS "
                  f"(jezero local eqc), ortho {ORTHO_RES_M} m grey HiRISE soc_006 (CTX 6 m "
                  f"fill), dtm {DTM_RES_M} m HiRISE soc_006 ({share:.0%} of pixels, CTX 20 m "
                  "fill) converted areoid -> sphere with the PDS MEGDR areoid."),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m layers.l2_regional.site_layer")
    p.add_argument("out", type=Path)
    p.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument("--areoid", type=Path, required=True)
    p.add_argument("--site-name", default="jezero_site")
    a = p.parse_args(argv)
    crs = site_crs(load_site("jezero"))
    to_geo = Transformer.from_crs(crs, geographic(), always_xy=True)
    bounds = site_bounds(crs, a.bbox)
    raw = {k: [a.raw / Path(n).name for n in v] for k, v in SOURCES.items()}
    a.out.mkdir(parents=True, exist_ok=True)
    stats = make_dtm(raw["dtm"], a.areoid, profile(crs, bounds, DTM_RES_M, 1, "float32",
                                                     DTM_NODATA), a.out / "dtm.tif", to_geo)
    make_ortho(raw["ortho"], profile(crs, bounds, ORTHO_RES_M, 1, "uint8", 0), a.out / "ortho.tif")
    finish_cog(a.out / "dtm.tif", compress="DEFLATE", predictor=3)
    finish_cog(a.out / "ortho.tif", compress="DEFLATE")
    doc = layer_doc(a.site_name, lonlat_bounds(to_geo, bounds), stats)
    layerjson.write(a.out / "layer.json", doc)
    print(json.dumps({"site_bounds_m": bounds, **stats}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
