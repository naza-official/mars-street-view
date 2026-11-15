import json

import pytest

from common import layerjson
from common.validate import Target, validate_layer

pd = pytest.importorskip("pandas")


def test_layer_json_is_contract_valid(l6_jezero_dir, l6_gale_dir):
    for d in (l6_jezero_dir, l6_gale_dir):
        doc = layerjson.read(d / "layer.json")
        assert layerjson.errors(doc) == []
        assert doc["owner"] == "B" and doc["validated"] is True
        assert validate_layer(Target(str(d))).ok


def test_meda_values_physical(l6_jezero_dir):
    d = pd.read_parquet(l6_jezero_dir / "meda_jezero_daily.parquet")
    p = d[d.variable == "pressure"]
    assert p["mean"].between(550, 900).all()
    assert p.groupby(p.sol // 100)["mean"].median().agg(lambda x: x.max() - x.min()) > 80
    rh = d[d.variable == "relative_humidity"]
    assert rh["max"].between(0, 100).all()
    g = d[d.variable == "ground_temp"]
    if not g.empty:
        assert g["min"].min() > 140 and g["max"].max() < 330
        assert (g["max"] - g["min"]).median() > 40
    assert d.rover_lon.dropna().between(77.2, 77.7).all()


def test_rad_dose_and_solar_cycle(l6_gale_dir):
    r = pd.read_parquet(l6_gale_dir / "rad_gale_daily.parquet")
    assert r.dose_b_ugy_day.median() == pytest.approx(225, abs=30)
    by = r.groupby(r.sol // 500 * 500).dose_b_ugy_day.median()
    assert by.loc[2000:3000].max() > by.loc[4000:].max() + 50
    s = json.loads((l6_gale_dir / "summary_gale.json").read_text())
    assert s["dose_rate_b"]["unit"] == "µGy/day"
