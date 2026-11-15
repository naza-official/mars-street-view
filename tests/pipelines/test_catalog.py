import json
from collections.abc import Iterator

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from common import storage
from pipelines import catalog

BUCKET = "mars-sv-test"


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


def stored(conn: object) -> dict:
    return json.loads(conn.get_object(Bucket=BUCKET, Key=catalog.INDEX_KEY)["Body"].read())


def test_update_creates_then_merges(s3: object) -> None:
    catalog.update(BUCKET, "l3_poses/jezero", "v1")
    catalog.update(BUCKET, "l1_global/global", "v1")
    assert stored(s3)["layers"] == {"l3_poses/jezero": "v1", "l1_global/global": "v1"}


def test_with_entry_rejects_bad_version() -> None:
    with pytest.raises(ValueError):
        catalog.with_entry(catalog.empty_index(), "l1_global/global", "1")


def test_write_refuses_stale_etag(s3: object) -> None:
    catalog.update(BUCKET, "l1_global/global", "v1")
    index, etag = catalog.read_index(BUCKET)
    catalog.update(BUCKET, "l3_poses/jezero", "v1")
    with pytest.raises(ClientError):
        catalog.write_index(BUCKET, catalog.with_entry(index, "l2_regional/jezero", "v1"), etag)
