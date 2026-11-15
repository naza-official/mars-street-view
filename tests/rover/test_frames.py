import numpy as np
import pytest
from b_common.mars import (
    R_MARS_SPHERE_M,
    lonlat_h_to_xyz,
    matrix_to_quat,
    ned_to_mars_fixed_matrix,
    quat_to_matrix,
    quat_to_rpy_deg,
    rover_quat_ned_to_mars_fixed,
    to_xyzw,
)

from common.crs import lonlat_to_xyz
from tests.rover.conftest import PILOT_LABEL_Q_WXYZ


def test_xyz_uses_common_sphere():
    a = lonlat_h_to_xyz(77.45, 18.44, -4252.8)
    b = lonlat_to_xyz(np.array(77.45), np.array(18.44), -4252.8)
    assert np.allclose(a, b)
    assert np.linalg.norm(a) == pytest.approx(R_MARS_SPHERE_M - 4252.8, abs=1e-6)


def test_ned_basis_orthonormal_and_down_points_to_centre():
    m = ned_to_mars_fixed_matrix(77.45, 18.44)
    assert np.allclose(m.T @ m, np.eye(3), atol=1e-12)
    up = lonlat_h_to_xyz(77.45, 18.44, 0) / R_MARS_SPHERE_M
    assert np.allclose(m[:, 2], -up, atol=1e-12)


def test_quat_roundtrip():
    q = np.array(PILOT_LABEL_Q_WXYZ) / np.linalg.norm(PILOT_LABEL_Q_WXYZ)
    assert np.allclose(matrix_to_quat(quat_to_matrix(q)), q, atol=1e-9)


def test_contract_quaternion_order_is_scalar_last():
    assert to_xyzw([1, 0, 0, 0]) == [0, 0, 0, 1]


def test_pilot_label_attitude_matches_mmgis():
    roll, pitch, yaw = quat_to_rpy_deg(PILOT_LABEL_Q_WXYZ)
    assert yaw == pytest.approx(130.8816, abs=0.01)
    assert roll == pytest.approx(-1.1817, abs=0.01)
    assert pitch == pytest.approx(-0.0251, abs=0.01)


def test_rover_forward_in_mars_fixed_points_to_heading():
    q = rover_quat_ned_to_mars_fixed(PILOT_LABEL_Q_WXYZ, 77.45, 18.44)
    fwd = quat_to_matrix(q) @ [1, 0, 0]
    ned = ned_to_mars_fixed_matrix(77.45, 18.44).T @ fwd
    assert np.degrees(np.arctan2(ned[1], ned[0])) % 360 == pytest.approx(130.88, abs=0.05)
