"""Image pixel -> ground point: CAHVORE back-projection + ray march over the PLACES orbital DEM.

Frames
  camera model : CAHVORE in the ROVER_NAV frame (x forward, y right, z down), pixel units of the
                 image as delivered (subframe and downsampling already applied)
  rover pose   : L3 (PLACES) attitude rover -> local north-east-down, position in PLACES map metres
  DEM          : PLACES m20_orbital_dem (1 m, heights above the areoid, same easting/northing as the
                 poses; the rover origin sits on this surface)
Easting/northing are local metres (dE = R cos(lat) dlon), so ENU offsets are plain differences.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

DEM_E0, DEM_N0 = 4340890.0, 1097070.0
DEM_NPY = Path.home() / "mars-data/work/places_dem.npy"


@dataclass
class Cahvore:
    c: np.ndarray
    a: np.ndarray
    h: np.ndarray
    v: np.ndarray
    o: np.ndarray
    r: np.ndarray
    e: np.ndarray
    linearity: float

    @classmethod
    def from_raw(cls, components: str) -> Cahvore:
        p = components.split(";")
        vec = [np.array([float(x) for x in s.strip("()").split(",")]) for s in p[:7]]
        mtype = float(p[7]) if len(p) > 7 else 1.0
        mparm = float(p[8]) if len(p) > 8 else 0.0
        lin = {1.0: 1.0, 2.0: 0.0}.get(mtype, mparm)
        return cls(*vec, linearity=lin)

    def rays(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pixel (x = sample, y = line) -> (entrance-pupil points, unit rays), rover frame."""
        x = np.asarray(x, float).ravel()
        y = np.asarray(y, float).ravel()
        u = self.v[None] - y[:, None] * self.a[None]
        w = self.h[None] - x[:, None] * self.a[None]
        rr = np.cross(u, w)
        avh1 = 1.0 / (self.a @ np.cross(self.v, self.h))
        rp = avh1 * rr
        zetap = rp @ self.o
        lam3 = rp - zetap[:, None] * self.o[None]
        lam = np.linalg.norm(lam3, axis=1)
        chip = lam / np.where(np.abs(zetap) < 1e-12, 1e-12, zetap)
        r0, r1, r2 = self.r
        chi = chip.copy()
        for _ in range(100):
            c2 = chi * chi
            deriv = (1 + r0) + 3 * r1 * c2 + 5 * r2 * c2 * c2
            dchi = ((1 + r0) * chi + r1 * chi * c2 + r2 * chi * c2 * c2 - chip) / deriv
            chi -= dchi
            if np.all(np.abs(dchi) < 1e-9):
                break
        lin = self.linearity
        if lin < -1e-15:
            theta = np.arcsin(np.clip(lin * chi, -1, 1)) / lin
        elif lin > 1e-15:
            theta = np.arctan(lin * chi) / lin
        else:
            theta = chi
        t2 = theta * theta
        sin_t = np.sin(theta)
        ratio = np.where(np.abs(sin_t) < 1e-12, 1.0, theta / np.where(sin_t == 0, 1, sin_t))
        s = (ratio - 1) * (self.e[0] + self.e[1] * t2 + self.e[2] * t2 * t2)
        cc = self.c[None] + s[:, None] * self.o[None]
        unit_lam = lam3 / np.where(lam[:, None] < 1e-12, 1, lam[:, None])
        ray = unit_lam * sin_t[:, None] + np.cos(theta)[:, None] * self.o[None]
        small = chip < 1e-8
        ray[small] = self.o
        return cc, ray / np.linalg.norm(ray, axis=1, keepdims=True)


def quat_xyzw_to_matrix(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


class Dem:
    def __init__(self, path: Path = DEM_NPY):
        self.z = np.load(path, mmap_mode="r")

    def height(self, e: np.ndarray, n: np.ndarray) -> np.ndarray:
        col = e - DEM_E0
        row = DEM_N0 - n
        c0 = np.clip(np.floor(col).astype(int), 0, self.z.shape[1] - 2)
        r0 = np.clip(np.floor(row).astype(int), 0, self.z.shape[0] - 2)
        fc, fr = np.clip(col - c0, 0, 1), np.clip(row - r0, 0, 1)
        z = self.z
        return ((z[r0, c0] * (1 - fc) + z[r0, c0 + 1] * fc) * (1 - fr)
                + (z[r0 + 1, c0] * (1 - fc) + z[r0 + 1, c0 + 1] * fc) * fr)


def cast(dem: Dem, cam_ned: np.ndarray, ray_ned: np.ndarray, rover_enu: np.ndarray,
         max_range: float = 60.0, step: float = 0.2) -> tuple[np.ndarray, np.ndarray]:
    """March rays from camera points (NED offsets from the rover origin) until they hit the DEM.

    Returns ground points (E, N, U in PLACES metres / areoid height) and the slant range; NaN where
    the ray never hits within max_range.
    """
    e0, n0, u0 = rover_enu
    m = len(ray_ned)
    hit_t = np.full(m, np.nan)
    prev_above = np.ones(m, bool)
    prev_t = np.zeros(m)
    prev_gap = np.full(m, np.nan)
    active = np.ones(m, bool)
    for t in np.arange(step, max_range + step, step):
        idx = np.nonzero(active)[0]
        if idx.size == 0:
            break
        p = cam_ned[idx] + t * ray_ned[idx]
        e, n, up = e0 + p[:, 1], n0 + p[:, 0], u0 - p[:, 2]
        gap = up - dem.height(e, n)
        below = gap <= 0
        if below.any():
            j = idx[below]
            g0, g1 = prev_gap[j], gap[below]
            frac = np.where(np.isfinite(g0) & (g0 != g1), g0 / (g0 - g1), 1.0)
            hit_t[j] = prev_t[j] + frac * step
            active[j] = False
        prev_gap[idx] = gap
        prev_t[idx] = t
        prev_above[idx] = ~below
    p = cam_ned + np.nan_to_num(hit_t)[:, None] * ray_ned
    pts = np.stack([e0 + p[:, 1], n0 + p[:, 0], u0 - p[:, 2]], axis=1)
    pts[np.isnan(hit_t)] = np.nan
    return pts, hit_t
