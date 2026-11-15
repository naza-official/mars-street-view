from pathlib import Path

import numpy as np
from affine import Affine
from scipy.ndimage import gaussian_filter
from scipy.ndimage import shift as nd_shift

from common.cog import write_cog
from common.crs import METRES_PER_DEGREE, global_cylindrical
from layers.l1_global import shift

PIXEL = METRES_PER_DEGREE / 128


def terrain(size: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return gaussian_filter(rng.normal(size=(size, size)), 3) * 4000


def test_peak_recovers_subpixel_offset() -> None:
    ref = terrain(160)
    mov = nd_shift(ref, (2.4, -3.6), order=3, mode="nearest")
    dy, dx, score = shift.peak(shift.ncc_surface(ref, mov, 8))
    assert abs(dy - 2.4) < 0.2 and abs(dx + 3.6) < 0.2
    assert score > 0.9


def write_pair(tmp_path: Path, offset_px: tuple[float, float]) -> tuple[Path, Path]:
    size, lon0, lat0 = 256, 76.5, 19.4
    height = terrain(size)
    shade = shift.hillshade(height, PIXEL, 135)
    gray = nd_shift(shade, offset_px, order=3, mode="nearest")
    gray = np.clip((gray - gray.min()) / np.ptp(gray) * 250 + 2, 1, 255).astype("uint8")
    x0 = (lon0 - 180.0) * METRES_PER_DEGREE
    y0 = lat0 * METRES_PER_DEGREE
    dtm = write_cog(tmp_path / "dtm.tif", height.astype("float32"),
                    Affine(PIXEL, 0, x0, 0, -PIXEL, y0), global_cylindrical(), -32768.0)
    big = np.repeat(np.repeat(gray, 2, axis=0), 2, axis=1)
    ortho = write_cog(tmp_path / "ortho.tif", np.stack([big] * 3),
                      Affine(PIXEL / 2, 0, x0, 0, -PIXEL / 2, y0), global_cylindrical(), 0)
    return dtm, ortho


def test_measure_reports_east_north_metres(tmp_path: Path) -> None:
    dtm, ortho = write_pair(tmp_path, (-2.0, 3.0))
    result = shift.measure(dtm, ortho, 77.5, 18.4, size_px=128, max_shift=8)
    assert result.azimuth_deg == 135
    assert abs(result.east_m - 3 * PIXEL) < 0.3 * PIXEL
    assert abs(result.north_m - 2 * PIXEL) < 0.3 * PIXEL


def test_measure_zero_shift(tmp_path: Path) -> None:
    dtm, ortho = write_pair(tmp_path, (0.0, 0.0))
    result = shift.measure(dtm, ortho, 77.5, 18.4, size_px=128, max_shift=8)
    assert result.magnitude_m < 0.3 * PIXEL
