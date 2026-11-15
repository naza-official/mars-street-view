"""Panorama maths for L4: MIPL cylindrical mosaic -> equirectangular -> multi-res cubemap tiles.

Frame of every output image: local level at the camera, azimuth clockwise from north (90 = E),
elevation up from the horizon. Equirect column 0 = azimuth 0; row 0 = elevation +90.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

FACES = {  # face -> (forward, right, up) unit vectors in local (north, east, up)
    "f": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),  # looks north
    "r": ((0, 1, 0), (-1, 0, 0), (0, 0, 1)),  # east
    "b": ((-1, 0, 0), (0, -1, 0), (0, 0, 1)),  # south
    "l": ((0, -1, 0), (1, 0, 0), (0, 0, 1)),  # west
    "u": ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),  # up, top of face = south
    "d": ((0, 0, -1), (0, 1, 0), (1, 0, 0)),  # down, top of face = north
}


def tone_map(radiance: np.ndarray, white_pct: float = 99.5, gamma: float = 2.2) -> np.ndarray:
    """(3, H, W) radiance with NaN -> (H, W, 4) uint8 RGBA; one white point keeps colour."""
    white = np.nanpercentile(radiance, white_pct)
    v = np.clip(np.nan_to_num(radiance / white, nan=0.0), 0, 1) ** (1 / gamma)
    rgb = (np.moveaxis(v, 0, -1) * 255 + 0.5).astype(np.uint8)
    alpha = np.where(np.isfinite(radiance).all(axis=0), 255, 0).astype(np.uint8)
    return np.dstack([rgb, alpha])


def cyl_to_equirect(
    img: np.ndarray, px_per_deg: float, zero_elev_line: float, start_azimuth: float = 0.0
) -> np.ndarray:
    """Place a MIPL cylindrical mosaic (H, W, C) into a full 360x180 equirect at the same scale."""
    h_in, w_in = img.shape[:2]
    W = int(round(360 * px_per_deg))
    H = W // 2
    out = np.zeros((H, W, img.shape[2]), img.dtype)
    rows = np.arange(H)
    elev = 90.0 - (rows + 0.5) * 180.0 / H
    src = np.rint(zero_elev_line - elev * px_per_deg).astype(int)
    ok = (src >= 0) & (src < h_in)
    shift = int(round(start_azimuth * W / 360.0))
    cols = (np.arange(W) - shift) % W
    cols_src = np.minimum((cols * w_in / W).astype(int), w_in - 1)
    out[rows[ok]] = img[src[ok]][:, cols_src]
    return out


def fill_sky(rgba: np.ndarray) -> np.ndarray:
    """RGB for JPEG tiles: transparent sky gets a flat colour from the top of the imaged band."""
    a = rgba[..., 3] > 0
    if not a.any():
        return rgba[..., :3]
    first = np.argmax(a, axis=0)  # first valid row per column
    cols = np.where(a.any(axis=0))[0]
    top = rgba[first[cols], cols, :3].astype(float)
    sky = np.median(top, axis=0) * 1.1
    rgb = rgba[..., :3].copy()
    rgb[~a] = np.clip(sky, 0, 255).astype(np.uint8)
    return rgb


def _sample_equirect(eq: np.ndarray, d: np.ndarray) -> np.ndarray:
    """Bilinear sample equirect (H, W, C) at directions d (..., 3) in (north, east, up)."""
    H, W = eq.shape[:2]
    az = np.degrees(np.arctan2(d[..., 1], d[..., 0])) % 360
    el = np.degrees(np.arcsin(np.clip(d[..., 2] / np.linalg.norm(d, axis=-1), -1, 1)))
    x = az / 360 * W - 0.5
    y = (90 - el) / 180 * H - 0.5
    x0 = np.floor(x).astype(int)
    y0 = np.clip(np.floor(y).astype(int), 0, H - 2)
    fx, fy = (x - x0)[..., None], np.clip(y - y0, 0, 1)[..., None]
    x0 %= W
    x1 = (x0 + 1) % W
    e = eq.astype(np.float32)
    v = (
        e[y0, x0] * (1 - fx) * (1 - fy)
        + e[y0, x1] * fx * (1 - fy)
        + e[y0 + 1, x0] * (1 - fx) * fy
        + e[y0 + 1, x1] * fx * fy
    )
    return np.clip(v + 0.5, 0, 255).astype(np.uint8)


def cube_face(eq: np.ndarray, face: str, size: int) -> np.ndarray:
    f, r, u = (np.array(v, float) for v in FACES[face])
    t = (np.arange(size) + 0.5) / size * 2 - 1
    xs, ys = np.meshgrid(t, -t)  # x right, y up
    d = f + xs[..., None] * r + ys[..., None] * u
    return _sample_equirect(eq, d)


def write_cubemap_tiles(
    eq_rgb: np.ndarray, out_dir: Path, face_size: int, tile: int = 512, quality: int = 88
) -> dict:
    """Write {level}/{face}_{y}_{x}.jpg (level 1 = smallest); return the pano.json block."""
    levels = []
    size = face_size
    while True:
        levels.append(size)
        if size <= tile:
            break
        size = max(tile, size // 2)
    levels = sorted(set(levels))
    for li, size in enumerate(levels, 1):
        for face in FACES:
            img = Image.fromarray(cube_face(eq_rgb, face, size))
            n = math.ceil(size / tile)
            for y in range(n):
                for x in range(n):
                    p = out_dir / str(li) / f"{face}_{y}_{x}.jpg"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    img.crop(
                        (x * tile, y * tile, min((x + 1) * tile, size), min((y + 1) * tile, size))
                    ).save(p, quality=quality)
    return {
        "projection": "cubemap",
        "tile_path": "{level}/{face}_{y}_{x}.jpg",
        "tile_size": tile,
        "levels": [{"level": i, "face_size": s} for i, s in enumerate(levels, 1)],
        "faces": {
            k: {"forward_neu": v[0], "right_neu": v[1], "up_neu": v[2]} for k, v in FACES.items()
        },
        "face_order_note": "f=N r=E b=S l=W u=up d=down; image x right, y down",
    }


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2))
