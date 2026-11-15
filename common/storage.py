import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

DEFAULT_BUCKET = "mars-sv-123412369922"
REGION = "us-west-2"
SCHEME = "s3"
PUBLISH_WORKERS = 16


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
    return boto3.client("s3", region_name=REGION,
                        config=Config(max_pool_connections=2 * PUBLISH_WORKERS))


def gdal_env() -> dict[str, str]:
    return {"AWS_REGION": REGION}


def rasterio_session() -> Any:
    from rasterio.session import AWSSession

    return AWSSession(boto3.Session(region_name=REGION))


def list_keys(uri: ObjectUri) -> list[str]:
    paginator = client().get_paginator("list_objects_v2")
    prefix = uri.key.rstrip("/") + "/" if uri.key else ""
    pages = paginator.paginate(Bucket=uri.bucket, Prefix=prefix)
    return [obj["Key"] for page in pages for obj in page.get("Contents", [])]


def read_bytes(uri: ObjectUri) -> bytes:
    return client().get_object(Bucket=uri.bucket, Key=uri.key)["Body"].read()


def upload_file(path: Path, uri: ObjectUri) -> None:
    client().upload_file(str(path), uri.bucket, uri.key)


MAX_SINGLE_PUT_BYTES = 5 * 1024**3
SHA256_META = "sha256"
LAYER_MANIFEST = "layer.json"
PUBLISHED_PREFIX = re.compile(r"^(orbital|rover)/[a-z0-9_]+/[a-z0-9_]+/v[1-9][0-9]*/$")


class AlreadyPublished(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_sha256(uri: ObjectUri) -> str | None:
    head = client().head_object(Bucket=uri.bucket, Key=uri.key)
    return head.get("Metadata", {}).get(SHA256_META)


def _is_precondition_failure(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in {"PreconditionFailed", "412"}


def put_new(path: Path, uri: ObjectUri) -> bool:
    size = path.stat().st_size
    if size > MAX_SINGLE_PUT_BYTES:
        raise ValueError(f"{path} is {size} bytes; single PUT limit is {MAX_SINGLE_PUT_BYTES}")
    digest = sha256_file(path)
    try:
        with path.open("rb") as body:
            client().put_object(Bucket=uri.bucket, Key=uri.key, Body=body, IfNoneMatch="*",
                                Metadata={SHA256_META: digest})
    except ClientError as error:
        if not _is_precondition_failure(error):
            raise
        if _existing_sha256(uri) != digest:
            raise AlreadyPublished(f"{uri.uri} exists with different content") from error
        return False
    return True


def _hidden(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def publish_order(root: Path) -> list[Path]:
    files = sorted(p for p in root.rglob("*") if p.is_file() and not _hidden(p, root))
    manifest = root / LAYER_MANIFEST
    if manifest not in files:
        raise FileNotFoundError(f"{manifest} missing")
    return [p for p in files if p != manifest] + [manifest]


def check_published_prefix(dest: ObjectUri) -> None:
    if not PUBLISHED_PREFIX.match(dest.key):
        raise ValueError(f"not a published layer prefix (<lane>/<layer>/<site>/vN/): {dest.uri}")


def publish_dir(root: Path, dest: ObjectUri,
                workers: int = PUBLISH_WORKERS) -> list[tuple[ObjectUri, bool]]:
    check_published_prefix(dest)
    *assets, manifest = publish_order(root)
    uris = [dest.child(p.relative_to(root).as_posix()) for p in assets]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        uploaded = list(pool.map(put_new, assets, uris))
    manifest_uri = dest.child(manifest.relative_to(root).as_posix())
    return [*zip(uris, uploaded, strict=True), (manifest_uri, put_new(manifest, manifest_uri))]
