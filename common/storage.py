import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3

DEFAULT_BUCKET = "mars-sv"
SSM_PREFIX = "/msv/r2"
ENV_KEYS = {
    "endpoint_url": "R2_ENDPOINT_URL",
    "access_key_id": "R2_ACCESS_KEY_ID",
    "secret_access_key": "R2_SECRET_ACCESS_KEY",
}
SCHEMES = ("r2", "s3")


@dataclass(frozen=True)
class ObjectUri:
    scheme: str
    bucket: str
    key: str

    @property
    def gdal_path(self) -> str:
        return f"/vsis3/{self.bucket}/{self.key}"

    def child(self, name: str) -> "ObjectUri":
        prefix = self.key.rstrip("/")
        return ObjectUri(self.scheme, self.bucket, f"{prefix}/{name}" if prefix else name)


@dataclass(frozen=True, repr=False)
class R2Config:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str

    def __repr__(self) -> str:
        return f"R2Config(endpoint_url={self.endpoint_url!r})"


def is_remote(uri: str) -> bool:
    return urlparse(uri).scheme in SCHEMES


def parse_uri(uri: str) -> ObjectUri:
    parsed = urlparse(uri)
    if parsed.scheme not in SCHEMES or not parsed.netloc:
        raise ValueError(f"not an r2:// or s3:// URI: {uri}")
    return ObjectUri(parsed.scheme, parsed.netloc, parsed.path.lstrip("/"))


def _from_env() -> R2Config | None:
    values = {field: os.environ.get(var) for field, var in ENV_KEYS.items()}
    return R2Config(**values) if all(values.values()) else None


def _from_ssm(prefix: str = SSM_PREFIX) -> R2Config:
    ssm = boto3.client("ssm")
    names = {field: f"{prefix}/{field}" for field in ENV_KEYS}
    response = ssm.get_parameters(Names=list(names.values()), WithDecryption=True)
    found = {p["Name"]: p["Value"] for p in response["Parameters"]}
    missing = [n for n in names.values() if n not in found]
    if missing:
        raise RuntimeError(f"SSM parameters missing: {missing}")
    return R2Config(**{field: found[name] for field, name in names.items()})


@cache
def r2_config() -> R2Config:
    return _from_env() or _from_ssm()


def client(scheme: str) -> Any:
    if scheme == "s3":
        return boto3.client("s3")
    config = r2_config()
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        region_name="auto",
    )


def gdal_env(scheme: str) -> dict[str, str]:
    if scheme == "s3":
        return {}
    config = r2_config()
    return {
        "AWS_S3_ENDPOINT": urlparse(config.endpoint_url).netloc,
        "AWS_ACCESS_KEY_ID": config.access_key_id,
        "AWS_SECRET_ACCESS_KEY": config.secret_access_key,
        "AWS_REGION": "auto",
        "AWS_VIRTUAL_HOSTING": "FALSE",
    }


def list_keys(uri: ObjectUri) -> list[str]:
    paginator = client(uri.scheme).get_paginator("list_objects_v2")
    prefix = uri.key.rstrip("/") + "/" if uri.key else ""
    pages = paginator.paginate(Bucket=uri.bucket, Prefix=prefix)
    return [obj["Key"] for page in pages for obj in page.get("Contents", [])]


def read_bytes(uri: ObjectUri) -> bytes:
    return client(uri.scheme).get_object(Bucket=uri.bucket, Key=uri.key)["Body"].read()


def upload_file(path: Path, uri: ObjectUri) -> None:
    client(uri.scheme).upload_file(str(path), uri.bucket, uri.key)
