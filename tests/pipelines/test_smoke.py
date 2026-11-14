import json
from pathlib import Path

import rasterio

from common import layerjson
from common.validate import Target, validate_layer
from pipelines import smoke


def test_smoke_layer_is_valid(tmp_path: Path) -> None:
    out = tmp_path / "layer"
    smoke.build(out, "ghcr.io/naza-official/mars-sv:test", today="2026-11-14")
    doc = json.loads((out / "layer.json").read_text())
    assert layerjson.errors(doc) == []
    assert (doc["layer"], doc["site"], doc["version"]) == ("_smoke", "global", "v1")
    report = validate_layer(Target(str(out)))
    assert report.ok, report.failures


def test_smoke_raster_spans_0_to_360(tmp_path: Path) -> None:
    smoke.build(tmp_path, "local", today="2026-11-14")
    with rasterio.open(tmp_path / "dtm.tif") as src:
        assert (src.width, src.height) == (smoke.WIDTH, smoke.HEIGHT)
        assert src.tags()["MSV_KIND"] == "elevation"
        assert abs(src.bounds.left + src.bounds.right) < 1e-3
