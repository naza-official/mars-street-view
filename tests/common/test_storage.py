import pytest

from common import storage


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    storage.r2_config.cache_clear()


def test_parse_r2_uri() -> None:
    uri = storage.parse_uri("r2://mars-sv/orbital/l1_global/global/v1/")
    assert (uri.scheme, uri.bucket, uri.key) == ("r2", "mars-sv", "orbital/l1_global/global/v1/")
    assert uri.child("layer.json").key == "orbital/l1_global/global/v1/layer.json"
    assert uri.child("dtm.tif").gdal_path == "/vsis3/mars-sv/orbital/l1_global/global/v1/dtm.tif"


@pytest.mark.parametrize("bad", ["/tmp/x", "https://a/b", "r2:///key"])
def test_parse_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        storage.parse_uri(bad)


def test_is_remote() -> None:
    assert storage.is_remote("s3://b/k")
    assert not storage.is_remote("tests/fixtures/x.tif")


def test_config_from_env_hides_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "s3cr3t")
    config = storage.r2_config()
    assert config.access_key_id == "key-id"
    assert "s3cr3t" not in repr(config)
    env = storage.gdal_env("r2")
    assert env["AWS_S3_ENDPOINT"] == "acct.r2.cloudflarestorage.com"
    assert env["AWS_VIRTUAL_HOSTING"] == "FALSE"


def test_config_falls_back_to_ssm(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in storage.ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)

    class FakeSsm:
        def get_parameters(self, Names: list[str], WithDecryption: bool) -> dict:
            assert WithDecryption
            return {"Parameters": [{"Name": n, "Value": n.rsplit("/", 1)[1]} for n in Names]}

    monkeypatch.setattr(storage.boto3, "client", lambda service: FakeSsm())
    config = storage.r2_config()
    assert config.endpoint_url == "endpoint_url"
    assert config.secret_access_key == "secret_access_key"


def test_ssm_missing_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in storage.ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)

    class EmptySsm:
        def get_parameters(self, Names: list[str], WithDecryption: bool) -> dict:
            return {"Parameters": []}

    monkeypatch.setattr(storage.boto3, "client", lambda service: EmptySsm())
    with pytest.raises(RuntimeError, match="SSM parameters missing"):
        storage.r2_config()
