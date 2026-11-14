from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

from common.cog import is_cog_layout, write_cog
from common.crs import global_cylindrical


def test_elevation_cog_structure(elevation_cog: Path) -> None:
    with rasterio.open(elevation_cog) as src:
        assert is_cog_layout(src)
        assert src.dtypes[0] == "float32"
        assert src.nodata == -32768.0
        assert src.block_shapes[0] == (512, 512)
        assert src.overviews(1)
        assert src.compression.value == "DEFLATE"
        assert src.tags()["MSV_KIND"] == "elevation"


def test_multiband_uint8(tmp_path: Path) -> None:
    data = np.zeros((3, 100, 100), dtype=np.uint8)
    path = write_cog(tmp_path / "rgb.tif", data, Affine(463, 0, 0, 0, -463, 0),
                     global_cylindrical(), 0)
    with rasterio.open(path) as src:
        assert src.count == 3
        assert is_cog_layout(src)
        assert src.nodata == 0


def test_roundtrip_values(tmp_path: Path) -> None:
    data = np.arange(64, dtype=np.float32).reshape(8, 8)
    path = write_cog(tmp_path / "v.tif", data, Affine(463, 0, 0, 0, -463, 0),
                     global_cylindrical(), -1.0)
    with rasterio.open(path) as src:
        np.testing.assert_array_equal(src.read(1), data)
