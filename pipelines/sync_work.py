import argparse
from collections.abc import Iterator
from pathlib import Path

from common.storage import ObjectUri, parse_uri, upload_file

SKIP_PARTS = {".snakemake", "__pycache__"}


def files_to_sync(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and not SKIP_PARTS.intersection(rel.parts):
            yield path


def plan(root: Path, dest: ObjectUri) -> list[tuple[Path, ObjectUri]]:
    if not dest.key.startswith("work/"):
        raise ValueError(f"sync_work only writes under work/: {dest.uri}")
    return [(p, dest.child(p.relative_to(root).as_posix())) for p in files_to_sync(root)]


def sync(root: Path, dest: ObjectUri) -> int:
    pairs = plan(root, dest)
    for path, uri in pairs:
        upload_file(path, uri)
    return len(pairs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload a run work dir to s3://<bucket>/work/<run_id>/")
    parser.add_argument("root", type=Path)
    parser.add_argument("dest")
    args = parser.parse_args(argv)
    count = sync(args.root, parse_uri(args.dest))
    print(f"synced {count} files to {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
