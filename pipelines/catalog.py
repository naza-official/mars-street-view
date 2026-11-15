import argparse
import json
import sys
from typing import Any

from botocore.exceptions import ClientError

from common import layerjson, storage

INDEX_KEY = "catalog/index.json"


def empty_index() -> dict[str, Any]:
    return {"schema": layerjson.SCHEMA_VERSION, "layers": {}}


def with_entry(index: dict[str, Any], entry: str, version: str) -> dict[str, Any]:
    updated = {"schema": index.get("schema", layerjson.SCHEMA_VERSION),
               "layers": {**index.get("layers", {}), entry: version}}
    problems = layerjson.errors(updated)
    if problems:
        raise ValueError("; ".join(problems))
    return updated


def read_index(bucket: str) -> tuple[dict[str, Any], str | None]:
    try:
        obj = storage.client().get_object(Bucket=bucket, Key=INDEX_KEY)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            return empty_index(), None
        raise
    return json.loads(obj["Body"].read()), obj["ETag"]


def write_index(bucket: str, index: dict[str, Any], etag: str | None) -> None:
    condition = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
    storage.client().put_object(Bucket=bucket, Key=INDEX_KEY, ContentType="application/json",
                                Body=json.dumps(index, indent=2).encode() + b"\n", **condition)


def update(bucket: str, entry: str, version: str) -> dict[str, Any]:
    index, etag = read_index(bucket)
    updated = with_entry(index, entry, version)
    write_index(bucket, updated, etag)
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipelines.catalog")
    parser.add_argument("entry", help="<layer>/<site>, e.g. l1_global/global")
    parser.add_argument("version", help="vN")
    parser.add_argument("--bucket", default=storage.DEFAULT_BUCKET)
    args = parser.parse_args(argv)
    print(json.dumps(update(args.bucket, args.entry, args.version), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
