import json
import struct

import numpy as np
import pytest

from common import layerjson
from common.validate import Target, validate_layer
from tests.rover.conftest import CONTRACT_STOP_KEYS, PILOT

pytest.importorskip("PIL")
pytest.importorskip("pandas")
import mesh as M  # noqa: E402
import pano as P  # noqa: E402
from b_common.mars import R_MARS_SPHERE_M, lonlat_h_to_xyz  # noqa: E402


def test_cyl_to_equirect_places_horizon_on_middle_row():
    cyl = np.zeros((300, 3600, 1), np.uint8)
    cyl[100] = 255
    eq = P.cyl_to_equirect(cyl, 10.0, 100.0)
    assert eq.shape[:2] == (1800, 3600)
    rows = np.where(eq[..., 0].max(axis=1) == 255)[0]
    assert abs(rows.mean() - 899.5) <= 1


def test_cyl_azimuth_kept_column0_north():
    cyl = np.zeros((10, 360, 1), np.uint8)
    cyl[:, 90] = 255
    eq = P.cyl_to_equirect(cyl, 1.0, 5.0)
    assert np.where(eq[90, :, 0] == 255)[0].tolist() == [90]


@pytest.mark.parametrize(
    "face,az,el",
    [("f", 0, 0), ("r", 90, 0), ("b", 180, 0), ("l", 270, 0), ("u", None, 90), ("d", None, -90)],
)
def test_cube_face_centres(face, az, el):
    eq = np.zeros((180, 360, 3), np.uint8)
    if az is None:
        if el > 0:
            eq[:3] = 255
        else:
            eq[-3:] = 255
    else:
        eq[85:95, max(az - 2, 0) : az + 3] = 255
        if az == 0:
            eq[85:95, -2:] = 255
    assert P.cube_face(eq, face, 64)[32, 32].max() == 255


def test_enu_matrix_orthonormal_and_up_is_radial():
    r = M.enu_matrix(77.45, 18.44)
    assert np.allclose(r.T @ r, np.eye(3), atol=1e-12)
    assert np.allclose(r[:, 2], lonlat_h_to_xyz(77.45, 18.44, 0) / R_MARS_SPHERE_M, atol=1e-12)


def test_site_ned_to_stop_enu_axes():
    ll = (77.45, 18.44)
    o = lonlat_h_to_xyz(*ll, -4252.8)
    p = M.site_to_stop_enu(np.array([[1.0, 2.0, -3.0]]), o, ll, o, ll)
    assert np.allclose(p, [[2.0, 1.0, 3.0]], atol=1e-6)


def test_layer_json_is_contract_valid(l4_dir):
    doc = layerjson.read(l4_dir / "layer.json")
    assert layerjson.errors(doc) == []
    assert doc["owner"] == "B" and doc["validated"] is True
    assert "rover/l3_poses/jezero/v1" in doc["depends_on"]
    assert validate_layer(Target(str(l4_dir))).ok


def test_pilot_stop_record_matches_contract(l4_dir):
    s = json.loads((l4_dir / PILOT / "stop.json").read_text())
    missing = [k for k in CONTRACT_STOP_KEYS if k not in s]
    assert missing == []
    xyz = np.array(s["position_xyz_m"])
    assert np.linalg.norm(xyz) == pytest.approx(
        R_MARS_SPHERE_M + s["height_above_sphere_m"], abs=0.01
    )
    assert np.linalg.norm(s["attitude_quat"]) == pytest.approx(1, abs=1e-6)
    base = f"rover/l4_streetview/jezero/{l4_dir.name}/{PILOT}/"
    assert s["panorama"] == base + "pano/"
    assert s["mesh_tileset"] == base + "tileset.json"


def test_pilot_panorama(l4_dir):
    s = json.loads((l4_dir / PILOT / "stop.json").read_text())
    p = json.loads((l4_dir / PILOT / "pano" / "pano.json").read_text())
    cam_h = p["camera"]["height_above_sphere_m"] - s["height_above_sphere_m"]
    assert 1.5 < cam_h < 2.3
    for lvl in p["cubemap"]["levels"]:
        for face in "frbldu":
            assert (l4_dir / PILOT / "pano" / str(lvl["level"]) / f"{face}_0_0.jpg").exists()


def test_pilot_mesh_tileset(l4_dir):
    ts = json.loads((l4_dir / PILOT / "tileset.json").read_text())
    assert ts["asset"]["version"] == "1.1"
    t = np.array(ts["root"]["transform"]).reshape(4, 4).T
    s = json.loads((l4_dir / PILOT / "stop.json").read_text())
    assert np.allclose(t[:3, 3], s["position_xyz_m"], atol=1e-3)
    glb = (l4_dir / PILOT / ts["root"]["content"]["uri"]).read_bytes()
    magic, ver, length = struct.unpack("<III", glb[:12])
    assert magic == 0x46546C67 and ver == 2 and length == len(glb)
    info = json.loads((l4_dir / PILOT / "mesh" / "mesh.json").read_text())
    lo, hi = np.array(info["bbox_enu_min"]), np.array(info["bbox_enu_max"])
    assert (lo[:2] < -50).all() and (hi[:2] > 50).all()
    assert -2 < lo[2] < 1
