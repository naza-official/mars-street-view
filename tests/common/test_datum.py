from pathlib import Path

import numpy as np
import pytest
from affine import Affine
from pyproj import Transformer

from common import datum
from common.cog import write_cog
from common.crs import geographic, global_cylindrical


@pytest.fixture
def areoid() -> datum.Areoid:
    rows = np.linspace(90, -90, datum.AREOID_LINES)[:, None]
    grid = np.repeat(rows * 10.0, datum.AREOID_SAMPLES, axis=1)
    return datum.Areoid(offset_m=grid)


def test_read_areoid_big_endian_with_offset(tmp_path: Path) -> None:
    raw = np.full((datum.AREOID_LINES, datum.AREOID_SAMPLES), 190, dtype=">i2")
    path = tmp_path / "areoid.img"
    raw.tofile(path)
    assert np.all(datum.read_areoid(path).offset_m == 0.0)


def test_offset_bilinear_and_wraps(areoid: datum.Areoid) -> None:
    at_cell = datum.areoid_offset(areoid, np.array([0.125]), np.array([89.875]))
    wrapped = datum.areoid_offset(areoid, np.array([360.125]), np.array([89.875]))
    negative = datum.areoid_offset(areoid, np.array([-359.875]), np.array([89.875]))
    assert at_cell[0] == pytest.approx(900.0)
    assert wrapped[0] == pytest.approx(at_cell[0])
    assert negative[0] == pytest.approx(at_cell[0])


def test_areoid_to_sphere_adds_offset(areoid: datum.Areoid) -> None:
    heights = np.array([-100.0])
    lon, lat = np.array([10.0]), np.array([89.875])
    assert datum.areoid_to_sphere(heights, lon, lat, areoid)[0] == pytest.approx(800.0)


def test_dem_geoid_command_reverses_mola() -> None:
    cmd = datum.dem_geoid_command(Path("in.tif"), Path("out/x"))
    assert cmd[:4] == ["dem_geoid", "--geoid", "mola", "--reverse-adjustment"]
    assert datum.dem_geoid_output(Path("out/x")) == Path("out/x-adj.tif")


def _pixel_centre_lonlat(transform: Affine, size: int) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.mgrid[0:size, 0:size] + 0.5
    xs, ys = transform @ (cols, rows)
    to_lonlat = Transformer.from_crs(global_cylindrical(), geographic(), always_xy=True)
    return to_lonlat.transform(xs, ys)


def test_check_adjustment_zero_residual(tmp_path: Path, areoid: datum.Areoid) -> None:
    transform = Affine(463.0, 0, -6000000.0, 0, -463.0, 1100000.0)
    before = np.zeros((64, 64), dtype=np.float32)
    lon, lat = _pixel_centre_lonlat(transform, 64)
    after = datum.areoid_offset(areoid, lon, lat).astype(np.float32)
    original = write_cog(tmp_path / "a.tif", before, transform, global_cylindrical(), -32768.0)
    adjusted = write_cog(tmp_path / "b.tif", after, transform, global_cylindrical(), -32768.0)
    stats = datum.check_adjustment(original, adjusted, areoid, n=200)
    assert stats["n"] == 200
    assert stats["max_abs_m"] < 0.01


def test_run_dem_geoid_needs_asp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(datum.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="dem_geoid"):
        datum.run_dem_geoid(Path("in.tif"), Path("out"))
