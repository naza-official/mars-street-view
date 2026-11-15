import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from common import layerjson
from common.sites import load_site

SLOPE_SCALE = 4.0
HEIGHT_OFFSET_M = 10_000.0
HEIGHT_SCALE = 10.0
COST = {"soil": 1.0, "bedrock": 1.5, "sand": 3.0, "big_rock": 8.0, "unknown": 2.5}
MAX_SLOPE_DEG = 25.0


def dem_on_grid(dtm_path: Path, cls_ds: rasterio.DatasetReader) -> np.ndarray:
    dem = np.full((cls_ds.height, cls_ds.width), np.nan, np.float32)
    with rasterio.open(dtm_path) as dtm:
        reproject(rasterio.band(dtm, 1), dem, dst_transform=cls_ds.transform, dst_crs=cls_ds.crs,
                  dst_nodata=np.nan, resampling=Resampling.average)
    return dem


def slope_deg(dem: np.ndarray, cell: float) -> np.ndarray:
    gy, gx = np.gradient(np.nan_to_num(dem, nan=float(np.nanmean(dem))), cell)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def pack(cls: np.ndarray, slope: np.ndarray, dem: np.ndarray) -> bytes:
    cls = np.where(np.isnan(dem), 255, cls).astype(np.uint8)
    s = np.clip(np.round(slope * SLOPE_SCALE), 0, 255).astype(np.uint8)
    h = np.clip(np.round((np.nan_to_num(dem) + HEIGHT_OFFSET_M) * HEIGHT_SCALE), 0, 65535)
    return cls.tobytes() + s.tobytes() + h.astype("<u2").tobytes()


def meta(ds: rasterio.DatasetReader, name: str) -> dict:
    t, crs = ds.transform, load_site("jezero").local_crs
    return {"name": name, "bin": f"{name}.bin", "width": ds.width, "height": ds.height,
            "cell_m": t.a, "x0": t.c, "y1": t.f,
            "eqc": {"lon_0": crs["lon_0"], "lat_0": crs["lat_0"], "lat_ts": crs["lat_ts"],
                    "radius_m": crs["radius_m"]},
            "layout": ["class u8", "slope u8 (deg x 4)", "height u16 LE ((m + 10000) x 10)"],
            "slope_scale": SLOPE_SCALE, "height_offset_m": HEIGHT_OFFSET_M,
            "height_scale": HEIGHT_SCALE, "classes": list(COST), "cost": COST,
            "max_slope_deg": MAX_SLOPE_DEG,
            "slope_penalty": "cost = class_cost * (1 + (slope_deg / 10)^2)"}


def main(classes: Path, dtm: Path, layer_dir: Path, name: str) -> int:
    with rasterio.open(classes) as ds:
        cls = ds.read(1)
        dem = dem_on_grid(dtm, ds)
        slope = slope_deg(dem, ds.transform.a)
        doc = meta(ds, name)
    (layer_dir / f"{name}.bin").write_bytes(pack(cls, slope, dem))
    (layer_dir / f"{name}.json").write_text(json.dumps(doc, indent=1))
    layer = layerjson.read(layer_dir / "layer.json")
    layer["assets"][f"{name}_costgrid"] = f"{name}.json"
    layer["assets"][f"{name}_costgrid_bin"] = f"{name}.bin"
    layerjson.write(layer_dir / "layer.json", layer)
    blocked = float(((slope > MAX_SLOPE_DEG) | (cls == 255)).mean())
    print(json.dumps({"grid": [doc["width"], doc["height"]], "blocked_share": round(blocked, 4),
                      "slope_p99": round(float(np.percentile(slope, 99)), 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]))
