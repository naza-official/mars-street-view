from pathlib import Path

import numpy as np
import rasterio
import rasterio.shutil
from affine import Affine
from pyproj import CRS
from rasterio.io import MemoryFile

BLOCK_SIZE = 512
FLOAT_PREDICTOR = 3
INT_PREDICTOR = 2


def cog_options(dtype: str) -> dict[str, object]:
    predictor = FLOAT_PREDICTOR if np.dtype(dtype).kind == "f" else INT_PREDICTOR
    return {
        "driver": "COG",
        "compress": "DEFLATE",
        "predictor": predictor,
        "blocksize": BLOCK_SIZE,
        "overview_resampling": "average",
        "bigtiff": "IF_SAFER",
    }


def _as_bands(data: np.ndarray) -> np.ndarray:
    return data[np.newaxis, ...] if data.ndim == 2 else data


def write_cog(path: Path, data: np.ndarray, transform: Affine, crs: CRS, nodata: float | int,
              tags: dict[str, str] | None = None) -> Path:
    bands = _as_bands(data)
    profile = {
        "driver": "GTiff",
        "width": bands.shape[2],
        "height": bands.shape[1],
        "count": bands.shape[0],
        "dtype": bands.dtype.name,
        "crs": rasterio.crs.CRS.from_wkt(crs.to_wkt()),
        "transform": transform,
        "nodata": nodata,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryFile() as memfile:
        with memfile.open(**profile) as tmp:
            tmp.write(bands)
            tmp.update_tags(**(tags or {}))
            rasterio.shutil.copy(tmp, path, **cog_options(bands.dtype.name))
    return path


def is_cog_layout(src: rasterio.DatasetReader) -> bool:
    return src.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
