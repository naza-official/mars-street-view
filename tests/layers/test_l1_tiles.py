import json
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from scipy.ndimage import gaussian_filter

from common import terrain
from common.cog import write_cog
from common.crs import MARS_RADIUS_M, METRES_PER_DEGREE, global_cylindrical
from common.terrain import TileKey
from layers.l1_global import tiles

ROOT = TileKey(6, 25, 27)
SIZE = 360


def write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    ext = tiles.GLOBAL.tile_extent(ROOT)
    rng = np.random.default_rng(0)
    height = (gaussian_filter(rng.normal(size=(SIZE, SIZE)), 3) * 3000 - 2500).astype("float32")
    pixel = METRES_PER_DEGREE / 128
    x0, y0 = (ext.west - 180) * METRES_PER_DEGREE, ext.north * METRES_PER_DEGREE
    dtm = write_cog(tmp_path / "dtm.tif", height, Affine(pixel, 0, x0, 0, -pixel, y0),
                    global_cylindrical(), -32768.0)
    rgb = rng.integers(0, 255, size=(3, 2 * SIZE, 2 * SIZE), dtype=np.uint8)
    ortho = write_cog(tmp_path / "ortho.tif", rgb, Affine(pixel / 2, 0, x0, 0, -pixel / 2, y0),
                      global_cylindrical(), 0)
    return dtm, ortho


def world_vertices(out: Path) -> dict[str, np.ndarray]:
    found: dict[str, np.ndarray] = {}

    def walk(node: dict, parent: np.ndarray) -> None:
        matrix = parent @ np.array(node.get("transform", np.eye(4).ravel())).reshape(4, 4).T
        uri = node.get("content", {}).get("uri")
        if uri:
            local = terrain.decode_positions((out / uri).read_bytes())
            found[uri] = local @ matrix[:3, :3].T + matrix[:3, 3]
        for child in node.get("children", []):
            walk(child, matrix)

    walk(json.loads((out / "tileset.json").read_text())["root"], np.eye(4))
    return found


def test_subtree_heights_match_corner_grid(tmp_path: Path) -> None:
    dtm, ortho = write_inputs(tmp_path)
    out = tmp_path / "tiles_out"
    stats = tiles.build(dtm, out, ortho, ROOT, workers=1, leaf_error_m=5.0)
    assert stats["tiles"] == 5
    assert stats["levels"][7]["geometric_error_max_m"] == 0.0
    assert stats["levels"][6]["geometric_error_max_m"] > 5.0
    world = world_vertices(out)
    assert len(world) == 5
    with rasterio.open(dtm) as src:
        corners = tiles.corner_grid(src.read(1), wrap_lon=False, poles=False)
    leaf_key = TileKey(7, 50, 55)
    ext = tiles.GLOBAL.tile_extent(leaf_key)
    xyz = world[f"tiles/{leaf_key.name}.glb"]
    radial = np.linalg.norm(xyz, axis=1) - MARS_RADIUS_M
    lon = np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0]))
    lat = np.degrees(np.arcsin(xyz[:, 2] / np.linalg.norm(xyz, axis=1)))
    col = np.round((lon - ext.west) / (ext.east - ext.west) * 180).astype(int)
    row = np.round((ext.north - lat) / (ext.north - ext.south) * 180).astype(int)
    expected = corners[row, 180 + col].astype(np.float64)
    surface = np.abs(radial - expected) <= 1.5
    skirt = expected - radial >= 10.0
    assert np.all(surface | skirt)
    assert surface.sum() > skirt.sum() > 0


def test_texture_embedded_in_tiles(tmp_path: Path) -> None:
    dtm, ortho = write_inputs(tmp_path)
    out = tmp_path / "tiles_out"
    tiles.build(dtm, out, ortho, ROOT, workers=1, leaf_error_m=5.0)
    doc, binary = terrain.read_glb((out / "tiles/7/50/54.glb").read_bytes())
    view = doc["bufferViews"][doc["images"][0]["bufferView"]]
    assert binary[view["byteOffset"]:view["byteOffset"] + 2] == b"\xff\xd8"


def test_corner_grid_poles_and_wrap() -> None:
    pix = np.arange(12, dtype=np.float32).reshape(3, 4)
    corners = tiles.corner_grid(pix, wrap_lon=True, poles=True)
    assert corners.shape == (4, 5)
    assert np.allclose(corners[0], pix[0].mean()) and np.allclose(corners[-1], pix[-1].mean())
    assert np.isclose(corners[1, 0], corners[1, -1])


def test_block_mean_halves() -> None:
    pix = np.arange(16, dtype=np.float32).reshape(4, 4)
    assert np.allclose(tiles.block_mean(pix, 2), [[2.5, 4.5], [10.5, 12.5]])


def test_global_key_count() -> None:
    assert len(tiles.tile_keys(tiles.GLOBAL, None)) == 2 * (4 ** 8 - 1) // 3


def test_assemble_with_two_global_roots() -> None:
    keys = [TileKey(0, 0, 1), TileKey(0, 0, 0)]
    records = {k: {"origin": [float(k.col), 0.0, 0.0], "box": [0.0] * 12, "quant_m": 0.5}
               for k in keys}
    tileset, externals = tiles.assemble(records, {k: 100.0 for k in keys})
    uris = [c["content"]["uri"] for c in tileset["root"]["children"]]
    assert uris == ["tiles/0/0/0.glb", "tiles/0/0/1.glb"]
    assert externals == {}
