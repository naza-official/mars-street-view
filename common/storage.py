from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3

DEFAULT_BUCKET = "mars-sv-123412369922"
REGION = "us-west-2"
SCHEME = "s3"


@dataclass(frozen=True)
class ObjectUri:
    bucket: str
    key: str

    @property
    def gdal_path(self) -> str:
        return f"/vsis3/{self.bucket}/{self.key}"

    @property
    def uri(self) -> str:
        return f"{SCHEME}://{self.bucket}/{self.key}"

    def child(self, name: str) -> "ObjectUri":
        prefix = self.key.rstrip("/")
        return ObjectUri(self.bucket, f"{prefix}/{name}" if prefix else name)


def is_remote(uri: str) -> bool:
    return urlparse(uri).scheme == SCHEME


def parse_uri(uri: str) -> ObjectUri:
    parsed = urlparse(uri)
    if parsed.scheme != SCHEME or not parsed.netloc:
        raise ValueError(f"not an s3:// URI: {uri}")
    return ObjectUri(parsed.netloc, parsed.path.lstrip("/"))


@cache
def client() -> Any:
    return boto3.client("s3", region_name=REGION)


def gdal_env() -> dict[str, str]:
    return {"AWS_REGION": REGION}


def list_keys(uri: ObjectUri) -> list[str]:
    paginator = client().get_paginator("list_objects_v2")
    prefix = uri.key.rstrip("/") + "/" if uri.key else ""
    pages = paginator.paginate(Bucket=uri.bucket, Prefix=prefix)
    return [obj["Key"] for page in pages for obj in page.get("Contents", [])]


def read_bytes(uri: ObjectUri) -> bytes:
    return client().get_object(Bucket=uri.bucket, Key=uri.key)["Body"].read()


def upload_file(path: Path, uri: ObjectUri) -> None:
    client().upload_file(str(path), uri.bucket, uri.key)
