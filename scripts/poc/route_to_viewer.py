import json
import shutil
import sys
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

from common import layerjson
from common.crs import BASE_CRS_CODE, DATUM_ID, geographic

LEGEND = {0: ["soil", "#D7A05A"], 1: ["bedrock", "#3C78E6"], 2: ["sand", "#FADC28"],
          3: ["big rock", "#E62828"], 4: ["unknown", "#5A5A5A"]}


def heights(dtm: rasterio.DatasetReader, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.array([v[0] for v in dtm.sample(zip(x, y, strict=True))], dtype=np.float64)


def class_cells(tif: rasterio.DatasetReader) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cls = tif.read(1)
    rows, cols = np.nonzero(cls >= 0)
    x, y = rasterio.transform.xy(tif.transform, rows, cols)
    return np.asarray(x), np.asarray(y), cls[rows, cols].astype(int)


def route_vertices(geojson: Path, to_site: Transformer) -> tuple[np.ndarray, np.ndarray]:
    coords = np.array(json.loads(geojson.read_text())["features"][0]["geometry"]["coordinates"])
    return to_site.transform(coords[:, 0], coords[:, 1])


def build(route_dir: Path, name: str, l2: Path, out: Path) -> dict:
    tif_path, gj_path = route_dir / f"{name}.tif", route_dir / f"{name}.geojson"
    with rasterio.open(tif_path) as tif, rasterio.open(l2 / "dtm.tif") as dtm:
        to_geo = Transformer.from_crs(tif.crs, geographic(), always_xy=True)
        to_site = Transformer.from_crs(geographic(), tif.crs, always_xy=True)
        cx, cy, ccls = class_cells(tif)
        rx, ry = route_vertices(gj_path, to_site)
        rcls = np.array([v[0] for v in tif.sample(zip(rx, ry, strict=True))], dtype=int)
        ch, rh = heights(dtm, cx, cy), heights(dtm, rx, ry)
    clon, clat = to_geo.transform(cx, cy)
    rlon, rlat = to_geo.transform(rx, ry)
    pack = lambda lo, la, h, c: [[round(float(a), 7), round(float(b), 7), round(float(z), 2),  # noqa: E731
                                  int(k)] for a, b, z, k in zip(lo, la, h, c, strict=True)]
    doc = {"name": name, "legend": LEGEND, "cell_m": 4,
           "summary": json.loads((route_dir / f"{name}.json").read_text()),
           "route": pack(rlon, rlat, rh, rcls), "cells": pack(clon, clat, ch, ccls)}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(doc, separators=(",", ":")))
    for suffix in (".geojson", ".tif", ".png"):
        if (route_dir / f"{name}{suffix}").exists():
            shutil.copy(route_dir / f"{name}{suffix}", out / f"{name}{suffix}")
    lon_all, lat_all = np.concatenate([clon, rlon]), np.concatenate([clat, rlat])
    pad = 1e-4
    return {"bounds": [round(float(lon_all.min()) - pad, 4), round(float(lat_all.min()) - pad, 4),
                       round(float(lon_all.max()) + pad, 4), round(float(lat_all.max()) + pad, 4)],
            "cells": int(len(ccls)), "vertices": int(len(rlon))}


def write_layer(out: Path, name: str, info: dict) -> None:
    doc = {"layer": "l9_routes", "site": "jezero", "version": "v1",
           "schema": layerjson.SCHEMA_VERSION, "owner": "B", "crs": BASE_CRS_CODE,
           "datum": DATUM_ID, "bounds_lonlat": info["bounds"],
           "assets": {f"{name}_route": f"{name}.json", f"{name}_geojson": f"{name}.geojson",
                      f"{name}_classes": f"{name}.tif"},
           "depends_on": ["orbital/l2_regional/jezero_site/v1", "rover/l9_terrain_db/jezero/v1"],
           "sources": [{"url": "https://github.com/naza-official/mars-street-view/pull/23",
                        "fetched": "2026-11-15", "license": "project code"}],
           "validated": True,
           "built_with": "ml/terrain_seg/corridor.py + scripts/poc/route_to_viewer.py",
           "notes": "Local PoC: B's route service on A's contract-format L2; not a contract layer."}
    layerjson.write(out / "layer.json", doc)


def main(route_dir: Path, name: str, l2: Path, out: Path) -> int:
    info = build(route_dir, name, l2, out)
    write_layer(out, name, info)
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])))
