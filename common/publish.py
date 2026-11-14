import argparse
import sys
from pathlib import Path

from common import storage
from common.validate import Target, print_report, validate_layer


class InvalidLayer(ValueError):
    pass


def publish_layer(root: Path, dest: storage.ObjectUri) -> list[tuple[storage.ObjectUri, bool]]:
    storage.check_published_prefix(dest)
    report = validate_layer(Target(str(root)))
    print_report(report)
    if not report.ok:
        raise InvalidLayer(f"{root} failed validation; not publishing")
    return storage.publish_dir(root, dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m common.publish",
        description="Validate a local layer folder, then upload it to an immutable "
                    "s3://<bucket>/<lane>/<layer>/<site>/vN/ prefix (layer.json last).",
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("dest", help="s3://<bucket>/<orbital|rover>/<layer>/<site>/vN/")
    args = parser.parse_args(argv)
    results = publish_layer(args.root, storage.parse_uri(args.dest))
    for uri, uploaded in results:
        print(f"{'put ' if uploaded else 'same'} {uri.uri}")
    print(f"published {len(results)} files to {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
