import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer

from common import layerjson
from common.crs import geographic

LEGEND = {0: ["soil", "#D7A05A"], 1: ["bedrock", "#3C78E6"], 2: ["sand", "#FADC28"],
          3: ["big rock", "#E62828"], 4: ["unknown", "#5A5A5A"]}
STEP = 4


def rgba(cls: np.ndarray) -> np.ndarray:
    out = np.zeros((*cls.shape, 4), np.uint8)
    for k, (_, hex_colour) in LEGEND.items():
        rgb = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
        out[cls == k] = [*rgb, 255]
    return out


def grid_vertices(src: rasterio.DatasetReader, dtm: rasterio.DatasetReader) -> dict:
    rows = np.unique(np.r_[np.arange(0, src.height, STEP), src.height])
    cols = np.unique(np.r_[np.arange(0, src.width, STEP), src.width])
    cc, rr = np.meshgrid(cols, rows)
    x, y = rasterio.transform.xy(src.transform, rr.ravel(), cc.ravel(), offset="ul")
    x, y = np.asarray(x), np.asarray(y)
    h = np.array([v[0] for v in dtm.sample(zip(x, y, strict=True))], dtype=np.float64)
    lon, lat = Transformer.from_crs(src.crs, geographic(), always_xy=True).transform(x, y)
    flat = np.column_stack([np.round(lon, 7), np.round(lat, 7), np.round(h, 2)]).ravel()
    return {"cols": int(len(cols)), "rows": int(len(rows)),
            "u": (cols / src.width).round(5).tolist(),
            "v": (1 - rows / src.height).round(5).tolist(),
            "vertices": flat.tolist(),
            "bounds": [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]}


def update_layer(layer_dir: Path, name: str, bounds: list[float]) -> None:
    doc = layerjson.read(layer_dir / "layer.json")
    doc["assets"][f"{name}_classmap"] = f"{name}.json"
    doc["assets"][f"{name}_classmap_png"] = f"{name}.png"
    doc["assets"][f"{name}_classes"] = f"{name}.tif"
    old = doc["bounds_lonlat"]
    doc["bounds_lonlat"] = [round(min(old[0], bounds[0]) - 1e-4, 4),
                            round(min(old[1], bounds[1]) - 1e-4, 4),
                            round(max(old[2], bounds[2]) + 1e-4, 4),
                            round(max(old[3], bounds[3]) + 1e-4, 4)]
    layerjson.write(layer_dir / "layer.json", doc)


def main(classes: Path, dtm_path: Path, layer_dir: Path, name: str) -> int:
    with rasterio.open(classes) as src, rasterio.open(dtm_path) as dtm:
        cls = src.read(1)
        mesh = grid_vertices(src, dtm)
    Image.fromarray(rgba(cls), "RGBA").save(layer_dir / f"{name}.png", optimize=True)
    (layer_dir / f"{name}.tif").write_bytes(classes.read_bytes())
    doc = {"name": name, "legend": LEGEND, "texture": f"{name}.png", "cell_m": 4.0, **mesh}
    (layer_dir / f"{name}.json").write_text(json.dumps(doc, separators=(",", ":")))
    update_layer(layer_dir, name, mesh["bounds"])
    print(json.dumps({"size": list(cls.shape), "grid": [mesh["cols"], mesh["rows"]],
                      "bounds": mesh["bounds"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]))
