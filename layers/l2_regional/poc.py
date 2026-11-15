import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from common import datum, layerjson
from common.crs import BASE_CRS_CODE, DATUM_ID, METRES_PER_DEGREE, global_cylindrical
from common.terrain import Extent, TileKey
from layers.l1_global.tiles import GLOBAL, SEGMENTS

DTM_NODATA = -32768.0
TINT = (1.0, 0.80, 0.64)
BLOCK_ROWS = 2048
LICENSE = "public domain (USGS Astrogeology)"
SOURCE_BASE = "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/"


def target_profile(extent: Extent, size: int, count: int, dtype: str, nodata: float | None) -> dict:
    pixel = (extent.east - extent.west) * METRES_PER_DEGREE / size
    transform = Affine(pixel, 0.0, (extent.west - 180.0) * METRES_PER_DEGREE,
                       0.0, -pixel, extent.north * METRES_PER_DEGREE)
    return {"driver": "GTiff", "width": size, "height": size, "count": count, "dtype": dtype,
            "crs": rasterio.crs.CRS.from_wkt(global_cylindrical().to_wkt()),
            "transform": transform, "nodata": nodata, "tiled": True, "blockxsize": 512,
            "blockysize": 512, "bigtiff": "IF_SAFER"}


def warped(path: Path, profile: dict, resampling: Resampling) -> WarpedVRT:
    src = rasterio.open(path)
    return WarpedVRT(src, crs=profile["crs"], transform=profile["transform"],
                     width=profile["width"], height=profile["height"], resampling=resampling,
                     src_nodata=src.nodata, nodata=src.nodata)


def block_lonlat(profile: dict, row0: int, rows: int) -> tuple[np.ndarray, np.ndarray]:
    t = profile["transform"]
    cols = np.arange(profile["width"]) + 0.5
    lines = np.arange(row0, row0 + rows) + 0.5
    lon = (t.c + cols * t.a) / METRES_PER_DEGREE + 180.0
    lat = (t.f + lines * t.e) / METRES_PER_DEGREE
    return np.broadcast_to(lon, (rows, lon.size)), np.broadcast_to(lat[:, None], (rows, lon.size))


def valid(block: np.ndarray, nodata: float | None) -> np.ndarray:
    ok = np.isfinite(block) & (block > -1e30)
    return ok if nodata is None else ok & (block != nodata)


def merged_block(vrts: list[WarpedVRT], window: Window) -> tuple[np.ndarray, np.ndarray]:
    out = np.full((window.height, window.width), np.nan, dtype=np.float64)
    for vrt in vrts:
        block = vrt.read(1, window=window).astype(np.float64)
        ok = valid(block, vrt.nodata) & np.isnan(out)
        out[ok] = block[ok]
    return out, ~np.isnan(out)


def make_dtm(fine: Path, coarse: Path, areoid: Path, profile: dict, out: Path) -> dict:
    model = datum.read_areoid(areoid)
    vrts = [warped(p, profile, Resampling.bilinear) for p in (fine, coarse)]
    filled = 0
    with rasterio.open(out, "w", **profile) as dst:
        for row0 in range(0, profile["height"], BLOCK_ROWS):
            window = Window(0, row0, profile["width"], min(BLOCK_ROWS, profile["height"] - row0))
            fine_ok = valid(vrts[0].read(1, window=window), vrts[0].nodata)
            heights, ok = merged_block(vrts, window)
            lon, lat = block_lonlat(profile, row0, window.height)
            heights[ok] += datum.areoid_offset(model, lon[ok], lat[ok])
            heights[~ok] = DTM_NODATA
            filled += int((ok & ~fine_ok).sum())
            dst.write(heights.astype(np.float32), 1, window=window)
    return {"pixels": profile["width"] * profile["height"], "ctx_filled": filled}


def make_ortho(fine: Path, coarse: Path, profile: dict, out: Path) -> None:
    vrts = [warped(fine, profile, Resampling.average), warped(coarse, profile, Resampling.average)]
    with rasterio.open(out, "w", **profile, compress="JPEG", jpeg_quality=88,
                       photometric="YCBCR") as dst:
        for row0 in range(0, profile["height"], BLOCK_ROWS):
            window = Window(0, row0, profile["width"], min(BLOCK_ROWS, profile["height"] - row0))
            grey, ok = merged_block(vrts, window)
            grey = np.where(ok, grey, 0.0)
            rgb = np.stack([np.clip(grey * k, 0, 255) for k in TINT]).astype(np.uint8)
            dst.write(rgb, window=window)


def layer_doc(extent: Extent, root: str, leaf_level: int, stats: dict, sources: list[str]) -> dict:
    return {
        "layer": "l2_regional", "site": "jezero_poc", "version": "v1",
        "schema": layerjson.SCHEMA_VERSION, "owner": "A", "crs": BASE_CRS_CODE,
        "datum": DATUM_ID,
        "bounds_lonlat": [extent.west, extent.south, extent.east, extent.north],
        "resolution_m": round((extent.east - extent.west) * METRES_PER_DEGREE
                              / (SEGMENTS << (leaf_level - int(root.split("/")[0]))), 3),
        "assets": {"dtm": "dtm.tif", "ortho": "ortho.tif", "tileset": "tileset.json"},
        "depends_on": [], "validated": True,
        "sources": [{"url": SOURCE_BASE + s, "fetched": "2026-11-15", "license": LICENSE}
                    for s in sources],
        "built_with": "local PoC (layers/l2_regional/poc.py)",
        "notes": (f"Local PoC, subtree {root}, leaves at level {leaf_level}. "
                  "HiRISE soc_006 1 m DTM, "
                  f"CTX soc_008 20 m DTM where HiRISE is empty ({stats['ctx_filled']} of "
                  f"{stats['pixels']} px); areoid -> sphere with the PDS MEGDR areoid "
                  "(common.datum). Ortho: HiRISE soc_006 25 cm (CTX 6 m fill), greyscale "
                  f"tinted {TINT}."),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m layers.l2_regional.poc")
    p.add_argument("root")
    p.add_argument("out", type=Path)
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument("--areoid", type=Path, required=True)
    p.add_argument("--leaf-level", type=int, default=15)
    p.add_argument("--texture-px", type=int, default=512)
    p.add_argument("--step", choices=["dtm", "ortho", "layer", "all"], default="all")
    a = p.parse_args(argv)
    key = TileKey(*map(int, a.root.split("/")))
    extent = GLOBAL.tile_extent(key)
    scale = 1 << (a.leaf_level - key.level)
    a.out.mkdir(parents=True, exist_ok=True)
    names = {"dtm": ("HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0"
                     "_blend40.tif",
                     "CTX/JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0.tif"),
             "ortho": ("HiRISE/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif",
                       "CTX/JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0.tif")}
    raw = {k: [a.raw / Path(n).name for n in v] for k, v in names.items()}
    stats_path = a.out.parent / f"{a.out.name}.dtm_stats.json"
    if a.step in ("dtm", "all"):
        profile = target_profile(extent, SEGMENTS * scale, 1, "float32", DTM_NODATA)
        stats = make_dtm(*raw["dtm"], a.areoid, profile, a.out / "dtm.tif")
        stats_path.write_text(json.dumps(stats) + "\n")
        print(json.dumps(stats))
    if a.step in ("ortho", "all"):
        make_ortho(*raw["ortho"], target_profile(extent, a.texture_px * scale, 3, "uint8", 0),
                   a.out / "ortho.tif")
        print("ortho done")
    if a.step in ("layer", "all"):
        stats = json.loads(stats_path.read_text())
        sources = [n for pair in names.values() for n in pair]
        doc = layer_doc(extent, a.root, a.leaf_level, stats, sources)
        layerjson.write(a.out / "layer.json", doc)
        print("layer.json written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
