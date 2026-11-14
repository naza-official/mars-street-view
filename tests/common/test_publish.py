import json
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from common import publish, storage
from pipelines import smoke

BUCKET = "mars-sv-test"
DEST = storage.ObjectUri(BUCKET, "orbital/_smoke/global/v1/")


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        storage.client.cache_clear()
        conn = boto3.client("s3", region_name=storage.REGION)
        conn.create_bucket(Bucket=BUCKET,
                           CreateBucketConfiguration={"LocationConstraint": storage.REGION})
        yield conn
    storage.client.cache_clear()


@pytest.fixture
def layer_dir(tmp_path: Path) -> Path:
    smoke.build(tmp_path / "layer", "test-image", today="2026-11-14")
    return tmp_path / "layer"


def test_put_new_is_idempotent_for_same_content(s3: object, tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("one")
    uri = storage.ObjectUri(BUCKET, "orbital/x/y/v1/a.txt")
    assert storage.put_new(path, uri) is True
    assert storage.put_new(path, uri) is False


def test_put_new_refuses_overwrite(s3: object, tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    uri = storage.ObjectUri(BUCKET, "orbital/x/y/v1/a.txt")
    path.write_text("one")
    storage.put_new(path, uri)
    path.write_text("two")
    with pytest.raises(storage.AlreadyPublished):
        storage.put_new(path, uri)


def test_put_new_rejects_large_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "big.bin"
    path.write_bytes(b"12345")
    monkeypatch.setattr(storage, "MAX_SINGLE_PUT_BYTES", 4)
    with pytest.raises(ValueError, match="single PUT"):
        storage.put_new(path, storage.ObjectUri(BUCKET, "orbital/x/y/v1/big.bin"))


def test_publish_order_puts_layer_json_last(layer_dir: Path) -> None:
    order = [p.name for p in storage.publish_order(layer_dir)]
    assert order[-1] == "layer.json"
    assert "dtm.tif" in order


def test_publish_order_requires_manifest(tmp_path: Path) -> None:
    (tmp_path / "x.tif").write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        storage.publish_order(tmp_path)


@pytest.mark.parametrize("key", [
    "work/run1/", "orbital/l1/global/", "orbital/l1/global/v0/", "catalog/v1/",
    "orbital/l1/global/v1", "other/l1/global/v1/",
])
def test_published_prefix_rejects(key: str) -> None:
    with pytest.raises(ValueError):
        storage.check_published_prefix(storage.ObjectUri(BUCKET, key))


def test_publish_layer_uploads_and_validates(s3: object, layer_dir: Path) -> None:
    results = publish.publish_layer(layer_dir, DEST)
    assert [uri.key.rsplit("/", 1)[1] for uri, _ in results] == ["dtm.tif", "layer.json"]
    doc = json.loads(storage.read_bytes(DEST.child("layer.json")))
    assert doc["built_with"] == "test-image"
    head = s3.head_object(Bucket=BUCKET, Key=DEST.child("dtm.tif").key)
    assert head["Metadata"]["sha256"] == storage.sha256_file(layer_dir / "dtm.tif")
    assert all(uploaded is False for _, uploaded in publish.publish_layer(layer_dir, DEST))


def test_publish_layer_refuses_invalid(s3: object, layer_dir: Path) -> None:
    (layer_dir / "dtm.tif").unlink()
    with pytest.raises(publish.InvalidLayer):
        publish.publish_layer(layer_dir, DEST)
    assert storage.list_keys(DEST) == []
