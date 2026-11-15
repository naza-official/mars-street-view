import pytest

from common import layerjson
from layers.l1_global import layer

CONV = {"east_m": 0.0, "north_m": 0.0, "magnitude_m": 0.0, "score": 0.99}
REG = {"east_m": -897.7, "north_m": 72.0, "magnitude_m": 900.6, "score": 0.521}
GEOID = {"n": 2000, "mean_m": 0.02, "max_abs_m": 0.8}
STATS = {"tiles": 43690, "bytes": 5.1e9, "leaf_error_m": 10.0}


def test_layer_doc_is_valid_and_records_shifts() -> None:
    doc = layer.layer_doc(CONV, REG, GEOID, STATS, "ghcr.io/naza-official/mars-sv:v0.4.0",
                          "2026-11-14")
    assert layerjson.errors(doc) == []
    assert "0.0 m E, 0.0 m N" in doc["notes"] and "-897.7 m E" in doc["notes"]
    assert doc["assets"]["tileset"] == "tileset.json"
    assert doc["bounds_lonlat"] == [0.0, -90.0, 360.0, 90.0]


def test_check_shift_rejects_large_conversion_shift() -> None:
    with pytest.raises(ValueError):
        layer.check_shift({**CONV, "magnitude_m": 300.0})
