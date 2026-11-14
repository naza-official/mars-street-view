import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "1.0"
NAME_PATTERN = "^[a-z0-9_]+$"
VERSION_PATTERN = "^v[1-9][0-9]*$"
LAYER_REF_PATTERN = "^(orbital|rover)/[a-z0-9_]+/[a-z0-9_]+/v[1-9][0-9]*$"

SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["url", "fetched", "license"],
    "properties": {
        "url": {"type": "string", "minLength": 1},
        "fetched": {"type": "string", "format": "date"},
        "license": {"type": "string", "minLength": 1},
    },
}

LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "layer.json",
    "type": "object",
    "required": [
        "layer", "site", "version", "schema", "owner", "crs", "datum",
        "bounds_lonlat", "assets", "sources", "validated",
    ],
    "properties": {
        "layer": {"type": "string", "pattern": NAME_PATTERN},
        "site": {"type": "string", "pattern": NAME_PATTERN},
        "version": {"type": "string", "pattern": VERSION_PATTERN},
        "schema": {"const": SCHEMA_VERSION},
        "owner": {"enum": ["A", "B"]},
        "crs": {"type": "string", "pattern": "^[A-Z0-9_]+:[0-9]+$"},
        "datum": {"const": "sphere_3396190"},
        "bounds_lonlat": {
            "type": "array",
            "prefixItems": [
                {"type": "number", "minimum": -180, "maximum": 360},
                {"type": "number", "minimum": -90, "maximum": 90},
                {"type": "number", "minimum": -180, "maximum": 360},
                {"type": "number", "minimum": -90, "maximum": 90},
            ],
            "minItems": 4,
            "maxItems": 4,
        },
        "resolution_m": {"type": "number", "exclusiveMinimum": 0},
        "assets": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": {"type": "string", "minLength": 1},
        },
        "depends_on": {
            "type": "array",
            "items": {"type": "string", "pattern": LAYER_REF_PATTERN},
        },
        "sources": {"type": "array", "items": SOURCE_SCHEMA},
        "validated": {"type": "boolean"},
        "built_with": {"type": "string"},
        "notes": {"type": "string"},
    },
}

INDEX_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "catalog/index.json",
    "type": "object",
    "required": ["schema", "layers"],
    "properties": {
        "schema": {"const": SCHEMA_VERSION},
        "layers": {
            "type": "object",
            "propertyNames": {"pattern": "^[a-z0-9_]+/[a-z0-9_]+$"},
            "additionalProperties": {"type": "string", "pattern": VERSION_PATTERN},
        },
    },
}


def schema_for(doc: dict[str, Any]) -> dict[str, Any]:
    return INDEX_SCHEMA if "layers" in doc and "layer" not in doc else LAYER_SCHEMA


def _bounds_errors(doc: dict[str, Any]) -> list[str]:
    bounds = doc.get("bounds_lonlat")
    if not isinstance(bounds, list) or len(bounds) != 4:
        return []
    west, south, east, north = bounds
    errors = []
    if south >= north:
        errors.append("bounds_lonlat: south must be less than north")
    if west == east:
        errors.append("bounds_lonlat: west equals east")
    return errors


def errors(doc: dict[str, Any]) -> list[str]:
    schema = schema_for(doc)
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    found = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    ]
    if schema is LAYER_SCHEMA:
        found += _bounds_errors(doc)
    return found


def read(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def write(path: Path, doc: dict[str, Any]) -> Path:
    problems = errors(doc)
    if problems:
        raise ValueError("; ".join(problems))
    Path(path).write_text(json.dumps(doc, indent=2) + "\n")
    return Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m common.layerjson")
    parser.add_argument("--check", type=Path, required=True,
                        help="layer.json or catalog/index.json to validate")
    args = parser.parse_args(argv)
    doc = read(args.check)
    problems = errors(doc)
    kind = schema_for(doc)["title"]
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"{'ok' if not problems else 'invalid'} {args.check} ({kind}, schema {SCHEMA_VERSION})")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
