import json
from pathlib import Path

import numpy as np
from affine import Affine
from pyproj import CRS

from common import validate
from common.cog import write_cog
from common.crs import global_cylindrical


def test_fixtures_pass(fixtures: Path) -> None:
    for name in ("mola_jezero.tif", "mdim_jezero.tif"):
        report = validate.validate(str(fixtures / name))
        assert report.ok, report.failures


def test_mola_fixture_heights_on_sphere(fixtures: Path) -> None:
    report = validate.validate(str(fixtures / "mola_jezero.tif"))
    assert any(line.startswith("height range") for line in report.info)
    assert report.lonlat is not None
    west, south, east, north = report.lonlat
    assert west < 77.5 < east and south < 18.4 < north


def test_elevation_cog_passes(elevation_cog: Path) -> None:
    assert validate.validate(str(elevation_cog)).ok


def test_earth_crs_fails(tmp_path: Path) -> None:
    data = np.zeros((16, 16), dtype=np.float32)
    path = write_cog(tmp_path / "earth.tif", data, Affine(463, 0, 0, 0, -463, 0),
                     CRS("EPSG:3857"), -1.0)
    report = validate.validate(str(path))
    assert not report.ok
    assert any("Mars sphere" in f for f in report.failures)


def test_strip_geotiff_without_nodata_fails(tmp_path: Path) -> None:
    import rasterio

    path = tmp_path / "strip.tif"
    profile = {"driver": "GTiff", "width": 600, "height": 600, "count": 1, "dtype": "int16",
               "crs": rasterio.crs.CRS.from_wkt(global_cylindrical().to_wkt()),
               "transform": Affine(463, 0, 0, 0, -463, 0)}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.zeros((1, 600, 600), dtype=np.int16))
    failures = " | ".join(validate.validate(str(path)).failures)
    for expected in ("nodata", "dtype", "COG", "tiled", "overviews"):
        assert expected in failures


def test_kilometre_heights_fail(tmp_path: Path) -> None:
    data = np.full((16, 16), 50000.0, dtype=np.float32)
    path = write_cog(tmp_path / "km.tif", data, Affine(463, 0, 0, 0, -463, 0),
                     global_cylindrical(), -32768.0, {"MSV_KIND": "elevation"})
    assert any("outside" in f for f in validate.validate(str(path)).failures)


def test_thermal_needs_units(tmp_path: Path) -> None:
    data = np.full((16, 16), 300.0, dtype=np.float32)
    path = write_cog(tmp_path / "t.tif", data, Affine(463, 0, 0, 0, -463, 0),
                     global_cylindrical(), -1.0, {"MSV_KIND": "thermal"})
    assert any("UNITS" in f for f in validate.validate(str(path)).failures)


def test_layer_dir_passes(layer_dir: Path) -> None:
    report = validate.validate(str(layer_dir))
    assert report.ok, report.failures


def test_layer_dir_missing_asset(layer_dir: Path) -> None:
    (layer_dir / "ortho.tif").unlink()
    assert any("missing" in f for f in validate.validate(str(layer_dir)).failures)


def test_layer_dir_bounds_mismatch(layer_dir: Path) -> None:
    doc = json.loads((layer_dir / "layer.json").read_text())
    doc["bounds_lonlat"] = [77.2, 18.2, 77.7, 18.7]
    (layer_dir / "layer.json").write_text(json.dumps(doc))
    assert any("outside layer bounds" in f for f in validate.validate(str(layer_dir)).failures)


def test_layer_dir_without_manifest(tmp_path: Path) -> None:
    assert validate.validate(str(tmp_path)).failures == ["layer.json missing"]


def test_bounds_inside_global_layer() -> None:
    assert validate.bounds_inside([-10, -5, 10, 5], [0, -90, 360, 90], 0.01)
    assert validate.bounds_inside([77.3, 18.3, 77.6, 18.6], [77.2, 18.2, 77.7, 18.7], 0.0)
    assert not validate.bounds_inside([77.0, 18.3, 77.6, 18.6], [77.2, 18.2, 77.7, 18.7], 0.01)


def test_cli_exit_codes(fixtures: Path, tmp_path: Path) -> None:
    assert validate.main([str(fixtures / "mola_jezero.tif")]) == 0
    assert validate.main([str(fixtures / "layer_example.json")]) == 0
    assert validate.main([str(tmp_path)]) == 1
