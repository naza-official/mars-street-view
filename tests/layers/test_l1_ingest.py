from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS, Transformer

from common import validate
from common.crs import geographic
from layers.l1_global import ingest

PPD = 2


def write_source(path: Path, count: int, dtype: str, nodata: float) -> np.ndarray:
    width, height = 360 * PPD, 180 * PPD
    lon_west = np.arange(width) / PPD - 180.0
    data = np.broadcast_to(np.round(lon_west).astype(dtype), (count, height, width)).copy()
    profile = {"driver": "GTiff", "width": width, "height": height, "count": count,
               "dtype": dtype, "nodata": nodata, "transform": Affine(0.5, 0, 0, 0, -0.5, 0)}
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(data)
    return data


def test_roll_puts_lon_zero_at_first_column(tmp_path: Path) -> None:
    write_source(tmp_path / "src.tif", 1, "int16", -32768)
    vrt = ingest.roll_vrt(tmp_path / "src.tif", PPD, tmp_path / "r.vrt", {"MSV_KIND": "x"})
    with rasterio.open(vrt) as ds:
        data = ds.read(1)
        to_geo = Transformer.from_crs(CRS.from_wkt(ds.crs.to_wkt()), geographic(), always_xy=True)
        lon, _ = to_geo.transform(*(ds.transform @ (0.5, 0.5)))
        assert ds.tags()["MSV_KIND"] == "x"
    assert data[0, 0] == 0 and data[0, 180 * PPD] == -180
    assert abs(lon - 0.5 / PPD) < 1e-9


def test_ortho_cog_validates(tmp_path: Path) -> None:
    write_source(tmp_path / "mdim.tif", 3, "uint8", 0)
    out = ingest.make_ortho(tmp_path / "mdim.tif", tmp_path / "w", tmp_path / "ortho.tif",
                            "2026-11-14", ppd=PPD)
    report = validate.validate_raster(str(out))
    assert report.ok, report.failures
    assert any("JPEG" in line for line in report.info)
    with rasterio.open(out) as ds:
        assert ds.tags()["MSV_KIND"] == "imagery"


def test_canonical_transform_edges() -> None:
    t = ingest.canonical_transform(128)
    assert t.c == -180.0 * ingest.METRES_PER_DEGREE
    assert np.isclose(t.a * 46080, 360.0 * ingest.METRES_PER_DEGREE)


def test_conversion_shift_zero_after_roll(tmp_path: Path) -> None:
    from scipy.ndimage import gaussian_filter

    from layers.l1_global import shift
    width, height = 360 * PPD * 8, 180 * PPD * 8
    rng = np.random.default_rng(5)
    img = (gaussian_filter(rng.normal(size=(height, width)), 2) * 400 + 128).clip(1, 255)
    profile = {"driver": "GTiff", "width": width, "height": height, "count": 3,
               "dtype": "uint8", "nodata": 0, "transform": Affine(0.5, 0, 0, 0, -0.5, 0)}
    with rasterio.open(tmp_path / "src.tif", "w", **profile) as ds:
        ds.write(np.stack([img.astype("uint8")] * 3))
    out = ingest.make_ortho(tmp_path / "src.tif", tmp_path / "w", tmp_path / "ortho.tif",
                            "2026-11-14", ppd=PPD * 8)
    result = shift.conversion_shift(tmp_path / "src.tif", out, PPD * 8, 77.5, 18.4,
                                    size_px=64, max_shift=4)
    assert result.magnitude_m < 0.05 * result.pixel_m
    assert result.score > 0.95
