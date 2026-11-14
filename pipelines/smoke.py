import argparse
import datetime as dt
import os
from pathlib import Path

import numpy as np
from affine import Affine

from common import layerjson
from common.cog import write_cog
from common.crs import BASE_CRS_CODE, DATUM_ID, METRES_PER_DEGREE, global_cylindrical

WIDTH, HEIGHT = 256, 128
NODATA = -32768.0
LAYER, SITE, VERSION = "_smoke", "global", "v1"
DTM_NAME = "dtm.tif"


def smoke_heights() -> np.ndarray:
    lat = np.linspace(90, -90, HEIGHT)[:, None]
    lon = np.linspace(0, 360, WIDTH)[None, :]
    return (2000.0 * np.cos(np.radians(lat)) * np.sin(np.radians(lon))).astype("float32")


def smoke_transform() -> Affine:
    pixel_x = 360.0 / WIDTH * METRES_PER_DEGREE
    pixel_y = 180.0 / HEIGHT * METRES_PER_DEGREE
    return Affine(pixel_x, 0, -180.0 * METRES_PER_DEGREE, 0, -pixel_y, 90.0 * METRES_PER_DEGREE)


def layer_doc(built_with: str, today: str) -> dict:
    return {
        "layer": LAYER,
        "site": SITE,
        "version": VERSION,
        "schema": layerjson.SCHEMA_VERSION,
        "owner": "A",
        "crs": BASE_CRS_CODE,
        "datum": DATUM_ID,
        "bounds_lonlat": [0.0, -90.0, 360.0, 90.0],
        "resolution_m": round(360.0 / WIDTH * METRES_PER_DEGREE, 3),
        "assets": {"dtm": DTM_NAME},
        "depends_on": [],
        "sources": [{"url": "synthetic:pipelines.smoke", "fetched": today,
                     "license": "public domain"}],
        "validated": True,
        "built_with": built_with,
        "notes": "M3 smoke layer: synthetic 256x128 elevation, not science data",
    }


def build(out_dir: Path, built_with: str, today: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_cog(out_dir / DTM_NAME, smoke_heights(), smoke_transform(), global_cylindrical(),
              NODATA, tags={"MSV_KIND": "elevation"})
    stamp = today or dt.date.today().isoformat()
    return layerjson.write(out_dir / "layer.json", layer_doc(built_with, stamp))



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipelines.smoke")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--built-with", default=os.environ.get("MSV_IMAGE", "local"))
    args = parser.parse_args(argv)
    print(f"wrote {build(args.out_dir, args.built_with)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
