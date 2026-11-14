import json
from pathlib import Path

import pytest

from common import layerjson


def test_contract_example_is_valid(layer_doc: dict) -> None:
    assert layerjson.errors(layer_doc) == []


def test_index_example_is_valid(fixtures: Path) -> None:
    doc = layerjson.read(fixtures / "index_example.json")
    assert layerjson.schema_for(doc) is layerjson.INDEX_SCHEMA
    assert layerjson.errors(doc) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "1"),
        ("schema", "2.0"),
        ("owner", "C"),
        ("datum", "wgs84"),
        ("bounds_lonlat", [77.2, 18.2, 77.7]),
        ("bounds_lonlat", [77.2, 18.7, 77.7, 18.2]),
        ("assets", {}),
        ("depends_on", ["l1_global/global/v1"]),
        ("sources", [{"url": "x", "fetched": "yesterday", "license": "pd"}]),
        ("validated", "yes"),
    ],
)
def test_bad_field_rejected(layer_doc: dict, field: str, value: object) -> None:
    assert layerjson.errors({**layer_doc, field: value})


def test_missing_required_rejected(layer_doc: dict) -> None:
    doc = {k: v for k, v in layer_doc.items() if k != "crs"}
    assert any("crs" in e for e in layerjson.errors(doc))


def test_bad_index_rejected() -> None:
    assert layerjson.errors({"schema": "1.0", "layers": {"l1_global": "v1"}})
    assert layerjson.errors({"schema": "1.0", "layers": {"l1_global/global": "latest"}})


def test_write_refuses_invalid(tmp_path: Path, layer_doc: dict) -> None:
    with pytest.raises(ValueError):
        layerjson.write(tmp_path / "layer.json", {**layer_doc, "owner": "C"})
    written = layerjson.write(tmp_path / "layer.json", layer_doc)
    assert json.loads(written.read_text()) == layer_doc


def test_cli_check(fixtures: Path, tmp_path: Path, layer_doc: dict,
                   capsys: pytest.CaptureFixture[str]) -> None:
    assert layerjson.main(["--check", str(fixtures / "layer_example.json")]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**layer_doc, "version": "one"}))
    assert layerjson.main(["--check", str(bad)]) == 1
    assert "FAIL version" in capsys.readouterr().out
