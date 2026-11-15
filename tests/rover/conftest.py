import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "layers"))
sys.path.insert(0, str(ROOT / "layers" / "l4_streetview"))

CONTRACT_STOP_KEYS = [
    "stop_id",
    "rover",
    "site",
    "drive",
    "sol",
    "position_xyz_m",
    "lon_e",
    "lat_n",
    "height_above_sphere_m",
    "attitude_quat",
    "pose_source",
    "alignment_rms_m",
    "panorama",
    "mesh_tileset",
    "next_stops",
]
PILOT = "m2020_site1_drive52"
PILOT_LABEL_Q_WXYZ = [0.415617, -0.00408664, -0.00947025, 0.909481]


def built(layer: str, site: str = "jezero") -> Path | None:
    from b_common.publish import latest_layer

    try:
        return latest_layer(layer, site)
    except FileNotFoundError:
        return None


@pytest.fixture
def l3_dir() -> Path:
    d = built("l3_poses")
    if d is None:
        pytest.skip("rover/l3_poses/jezero not built (run layers/l3_poses/build.py)")
    return d


@pytest.fixture
def l4_dir() -> Path:
    d = built("l4_streetview")
    if d is None:
        pytest.skip("rover/l4_streetview/jezero not built (run layers/l4_streetview/build.py)")
    return d


@pytest.fixture
def l6_jezero_dir() -> Path:
    d = built("l6_radiation_atmo", "jezero")
    if d is None:
        pytest.skip("rover/l6_radiation_atmo/jezero not built")
    return d


@pytest.fixture
def l6_gale_dir() -> Path:
    d = built("l6_radiation_atmo", "gale")
    if d is None:
        pytest.skip("rover/l6_radiation_atmo/gale not built")
    return d
