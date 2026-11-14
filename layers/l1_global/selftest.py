import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

from layers.l1_global import ingest

PPD = 4


def synthetic_mola(path: Path) -> Path:
    width, height = 360 * PPD, 180 * PPD
    rows, cols = np.mgrid[0:height, 0:width]
    data = (2000 * np.sin(np.radians(cols / PPD)) * np.cos(np.radians(rows / PPD - 90)))
    profile = {"driver": "GTiff", "width": width, "height": height, "count": 1,
               "dtype": "int16", "nodata": -32768, "transform": Affine(1, 0, 0, 0, -1, 0)}
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(data.astype("int16")[None])
    return path


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        areoid = ingest.fetch_areoid(root / "areoid.img")
        mola = synthetic_mola(root / "mola.tif")
        check = ingest.make_dtm(mola, areoid, root / "work", root / "dtm.tif", "selftest", PPD)
        with rasterio.open(root / "dtm.tif") as ds:
            west, _, east, _ = ds.bounds
        print(json.dumps({"dem_geoid_vs_pds": check, "x_bounds": [west, east]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
