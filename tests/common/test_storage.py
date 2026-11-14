import pytest

from common import storage


def test_parse_s3_uri() -> None:
    uri = storage.parse_uri("s3://mars-sv-123412369922/orbital/l1_global/global/v1/")
    assert (uri.bucket, uri.key) == ("mars-sv-123412369922", "orbital/l1_global/global/v1/")
    child = uri.child("dtm.tif")
    assert child.key == "orbital/l1_global/global/v1/dtm.tif"
    assert child.gdal_path == "/vsis3/mars-sv-123412369922/orbital/l1_global/global/v1/dtm.tif"
    assert child.uri == "s3://mars-sv-123412369922/orbital/l1_global/global/v1/dtm.tif"


@pytest.mark.parametrize("bad", ["/tmp/x", "https://a/b", "s3:///key", "r2://mars-sv/x"])
def test_parse_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        storage.parse_uri(bad)


def test_is_remote() -> None:
    assert storage.is_remote("s3://b/k")
    assert not storage.is_remote("r2://b/k")
    assert not storage.is_remote("tests/fixtures/x.tif")


def test_gdal_env_pins_region() -> None:
    assert storage.gdal_env() == {"AWS_REGION": "us-west-2"}


def test_list_keys_uses_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {}

    class FakePaginator:
        def paginate(self, Bucket: str, Prefix: str) -> list[dict]:
            calls.update(bucket=Bucket, prefix=Prefix)
            return [{"Contents": [{"Key": f"{Prefix}layer.json"}]}, {}]

    class FakeS3:
        def get_paginator(self, name: str) -> FakePaginator:
            return FakePaginator()

    monkeypatch.setattr(storage, "client", lambda: FakeS3())
    keys = storage.list_keys(storage.parse_uri("s3://b/orbital/x/v1"))
    assert calls == {"bucket": "b", "prefix": "orbital/x/v1/"}
    assert keys == ["orbital/x/v1/layer.json"]
