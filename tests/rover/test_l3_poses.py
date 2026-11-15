import json

import numpy as np
import pytest

from common import layerjson
from common.sites import load_site
from common.validate import Target, validate_layer
from tests.rover.conftest import PILOT

pd = pytest.importorskip("pandas")


def test_layer_json_is_contract_valid(l3_dir):
    doc = layerjson.read(l3_dir / "layer.json")
    assert layerjson.errors(doc) == []
    assert doc["owner"] == "B" and doc["layer"] == "l3_poses" and doc["validated"] is True
    assert validate_layer(Target(str(l3_dir))).ok


def test_poses_inside_site_and_on_sphere(l3_dir):
    w, s, e, n = load_site("jezero").bbox_lonlat
    c = pd.read_csv(l3_dir / "poses.csv")
    assert list(c.columns) == [
        "site",
        "drive",
        "lon",
        "lat",
        "height",
        "roll",
        "pitch",
        "yaw",
        "source",
    ]
    assert c[["lon", "lat", "height"]].notna().all().all()
    assert c.lon.between(w, e).all() and c.lat.between(s, n).all()
    assert c.height.between(-5000, -1000).all()
    assert not c.duplicated(["site", "drive"]).any()
    p = pd.read_parquet(l3_dir / "poses.parquet")
    r = np.linalg.norm(p[["x_m", "y_m", "z_m"]].to_numpy(), axis=1)
    assert np.allclose(r, 3396190.0 + p.height_above_sphere_m, atol=1e-3)


def test_pilot_attitude_known_and_scalar_last(l3_dir):
    p = pd.read_parquet(l3_dir / "poses.parquet")
    pilot = p[p.stop_id == PILOT].iloc[0]
    assert not pilot.attitude_unknown
    q = np.array(pilot.attitude_quat)
    assert np.linalg.norm(q) == pytest.approx(1, abs=1e-6)
    assert pilot.yaw == pytest.approx(130.88, abs=0.05)


def test_mmgis_crosscheck(l3_dir):
    rep = json.loads((l3_dir / "report.json").read_text())
    assert rep["mmgis_matched"] > 600
    assert rep["mmgis_horiz_p95_m"] < 0.05
    assert rep["mmgis_elev_areoid_max_abs_m"] < 0.01
    assert rep["mmgis_yaw_p95_abs_deg"] < 2.0
