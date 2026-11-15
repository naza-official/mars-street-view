import json

import numpy as np
from scipy.ndimage import gaussian_filter

from common import terrain
from common.crs import MARS_RADIUS_M
from common.terrain import Extent, TileGrid, TileKey

EXTENT = Extent(77.0, 18.0, 78.0, 19.0)


def bumpy(n: int, seed: int = 0, scale: float = 800.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (gaussian_filter(rng.normal(size=(n, n)), 4) * scale - 2500).astype(np.float32)


def test_tile_grid_extents_and_children() -> None:
    grid = TileGrid(Extent(0.0, -90.0, 360.0, 90.0), rows0=1, cols0=2)
    assert grid.tile_extent(TileKey(0, 0, 1)) == Extent(180.0, -90.0, 360.0, 90.0)
    assert grid.tile_extent(TileKey(1, 1, 0)) == Extent(0.0, -90.0, 90.0, 0.0)
    child = TileKey(3, 5, 6)
    assert child in TileKey(2, 2, 3).children()
    assert child.ancestor(1) == TileKey(1, 1, 1)


def test_quantize_round_trip_within_half_step() -> None:
    pts = np.random.default_rng(1).uniform(-40000, 40000, size=(500, 3))
    q = terrain.quantize(pts)
    err = np.abs(terrain.dequantize(q) - pts).max(axis=0)
    assert np.all(err <= q.step / 2 + 1e-9)
    assert q.max_error_m == np.linalg.norm(q.step) / 2


def test_glb_positions_round_trip() -> None:
    local = np.random.default_rng(2).uniform(-30000, 30000, size=(300, 3))
    uv = np.random.default_rng(3).uniform(0, 1, size=(300, 2))
    tris = np.arange(297, dtype=np.uint32).reshape(-1, 3)
    glb = terrain.encode_glb(local, uv, tris, b"\xff\xd8fakejpeg\xff\xd9")
    assert len(glb) % 4 == 0
    doc, _ = terrain.read_glb(glb)
    assert doc["extensionsRequired"] == ["KHR_mesh_quantization"]
    assert doc["images"][0]["mimeType"] == "image/jpeg"
    q = terrain.quantize(terrain.z_up_to_y_up(local))
    assert np.abs(terrain.decode_positions(glb) - local).max() <= q.max_error_m + 1e-6


def test_simplified_mesh_rows_follow_raster_order() -> None:
    heights = bumpy(65).astype(np.float64)
    mesh = terrain.simplified_mesh(heights, 1.0)
    tin_heights = heights[mesh.rows, mesh.cols]
    assert mesh.rows.min() == 0 and mesh.rows.max() == 64
    assert {(0, 0), (0, 64), (64, 0), (64, 64)} <= set(zip(mesh.rows, mesh.cols, strict=True))
    assert np.isfinite(tin_heights).all()


def test_skirts_hang_from_every_boundary_vertex() -> None:
    mesh = terrain.with_skirts(terrain.regular_mesh(5))
    assert mesh.skirt.sum() == 16
    assert len(mesh.triangles) == 2 * 16 + 2 * 16
    border = (mesh.rows == 0) | (mesh.rows == 4) | (mesh.cols == 0) | (mesh.cols == 4)
    assert np.all(border[mesh.skirt])


def test_curved_heights_keep_chords_near_sphere() -> None:
    n = 181
    corners = np.zeros((n, n), np.float32)
    max_error = 2.0
    mesh = terrain.simplified_mesh(terrain.curved_heights(corners, EXTENT), max_error)
    xyz = terrain.mesh_xyz(mesh, corners, EXTENT, 0.0)
    centroids = xyz[mesh.triangles].mean(axis=1)
    sag = MARS_RADIUS_M - np.linalg.norm(centroids, axis=1)
    assert sag.max() <= 1.5 * max_error
    assert len(mesh.rows) < n * n / 4


def test_refinement_error_zero_for_bilinear_surface() -> None:
    r, c = np.mgrid[0:9, 0:9]
    coarse = (3.0 * r + 2.0 * c).astype(np.float32)
    fine = terrain.upsample_corners(coarse)
    assert terrain.refinement_error(coarse, fine) == 0.0
    fine[3, 3] += 5
    assert terrain.refinement_error(coarse, fine) == 5.0


def test_obb_contains_points() -> None:
    local = np.random.default_rng(4).uniform(-1000, 1000, size=(200, 3))
    box = terrain.obb(local, terrain.up_vector(77.5, 18.5))
    centre, half = np.array(box[:3]), np.array(box[3:]).reshape(3, 3)
    axes = half / np.linalg.norm(half, axis=1, keepdims=True)
    proj = np.abs((local - centre) @ axes.T)
    assert np.all(proj <= np.linalg.norm(half, axis=1) + 1e-6)
    json.dumps(box)
