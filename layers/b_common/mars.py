"""Rover-frame helpers for Person B's layers, on top of `common.crs` (L0).

`common.crs` owns the sphere and lon/lat/height -> Mars-fixed XYZ; this module adds the local
north-east-down frame and quaternion conversions that only the rover lane needs.
Quaternions are scalar-first [w, x, y, z] internally; `to_xyzw` gives the contract order.
"""

from __future__ import annotations

import numpy as np

from common.crs import MARS_RADIUS_M, lonlat_to_xyz

R_MARS_SPHERE_M = MARS_RADIUS_M


def height_above_sphere(radius_m):
    return np.asarray(radius_m, dtype=float) - MARS_RADIUS_M


def lonlat_h_to_xyz(lon_e_deg, lat_n_deg, h_m):
    return lonlat_to_xyz(np.asarray(lon_e_deg, float), np.asarray(lat_n_deg, float), h_m)


def to_xyzw(q_wxyz) -> list[float]:
    """Scalar-first -> scalar-last, the stop-record contract order ([0, 0, 0, 1] = identity)."""
    w, x, y, z = (float(v) for v in q_wxyz)
    return [x, y, z, w]


def ned_to_mars_fixed_matrix(lon_e_deg: float, lat_n_deg: float) -> np.ndarray:
    """Columns = local North, East, Down unit vectors expressed in Mars-fixed XYZ."""
    lo, la = np.radians(lon_e_deg), np.radians(lat_n_deg)
    n = [-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)]
    e = [-np.sin(lo), np.cos(lo), 0.0]
    d = [-np.cos(la) * np.cos(lo), -np.cos(la) * np.sin(lo), -np.sin(la)]
    return np.array([n, e, d]).T


def quat_to_matrix(q):
    """Scalar-first unit quaternion [w, x, y, z] -> 3x3 rotation matrix."""
    w, x, y, z = q / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def matrix_to_quat(m) -> np.ndarray:
    """3x3 rotation matrix -> scalar-first quaternion with w >= 0."""
    t = np.trace(m)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    q = np.array(q)
    return q if q[0] >= 0 else -q


def rover_quat_ned_to_mars_fixed(q_ned, lon_e_deg, lat_n_deg) -> np.ndarray:
    """PLACES rover attitude (rover -> local level NED, scalar first) -> rover -> Mars-fixed."""
    return matrix_to_quat(
        ned_to_mars_fixed_matrix(lon_e_deg, lat_n_deg) @ quat_to_matrix(np.asarray(q_ned, float))
    )


def quat_to_rpy_deg(q):
    """Scalar-first quaternion (rover -> NED) -> roll, pitch, yaw in degrees (PLACES, Z-Y-X)."""
    m = quat_to_matrix(np.asarray(q, float))
    yaw = np.degrees(np.arctan2(m[1, 0], m[0, 0]))
    pitch = np.degrees(np.arcsin(-np.clip(m[2, 0], -1, 1)))
    roll = np.degrees(np.arctan2(m[2, 1], m[2, 2]))
    return roll, pitch, yaw % 360
