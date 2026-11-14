import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from affine import Affine

from common.cog import write_cog
from common.crs import global_cylindrical

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def layer_doc() -> dict:
    return json.loads((FIXTURES / "layer_example.json").read_text())


@pytest.fixture
def elevation_cog(tmp_path: Path) -> Path:
    data = np.linspace(-3000, -2000, 600 * 600, dtype=np.float32).reshape(600, 600)
    transform = Affine(463.0, 0, -6000000.0, 0, -463.0, 1100000.0)
    return write_cog(tmp_path / "dtm.tif", data, transform, global_cylindrical(), -32768.0,
                     {"MSV_KIND": "elevation", "UNITS": "m"})


@pytest.fixture
def layer_dir(tmp_path: Path, layer_doc: dict) -> Path:
    root = tmp_path / "l1_global" / "jezero" / "v1"
    root.mkdir(parents=True)
    shutil.copy(FIXTURES / "mola_jezero.tif", root / "dtm.tif")
    shutil.copy(FIXTURES / "mdim_jezero.tif", root / "ortho.tif")
    doc = {**layer_doc, "layer": "l1_global", "bounds_lonlat": [76.0, 17.0, 79.0, 20.0],
           "resolution_m": 463.0, "assets": {"dtm": "dtm.tif", "ortho": "ortho.tif"}}
    (root / "layer.json").write_text(json.dumps(doc))
    return root
