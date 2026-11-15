"""Write and validate B-lane outputs in the contract layout (docs/contracts.md).

Local mirror of the bucket: $MSV_DATA/rover/<layer>/<site>/vN/ ; layer.json schema 1.0 is
written through `common.layerjson` (schema-checked) and the folder is then run through
`common.validate.validate_layer`; `validated` is true only if that passes.
"""

from __future__ import annotations

import json
from pathlib import Path

from b_common.pds import data_root

from common import layerjson
from common.crs import BASE_CRS_CODE, DATUM_ID
from common.validate import Target, validate_layer


def layer_dir(layer: str, site: str, version: str) -> Path:
    p = data_root() / "rover" / layer / site / version
    p.mkdir(parents=True, exist_ok=True)
    return p


def latest_layer(layer: str, site: str) -> Path:
    """Highest vN of a rover layer that has a layer.json (a version is ready only then)."""
    root = data_root() / "rover" / layer / site
    vs = sorted(
        (p for p in root.glob("v*") if (p / "layer.json").exists()), key=lambda p: int(p.name[1:])
    )
    if not vs:
        raise FileNotFoundError(f"rover/{layer}/{site}: no built version")
    return vs[-1]


def contract_sources(fetch_manifests: list[Path]) -> list[dict]:
    """fetch_manifest.json entries -> layer.json `sources` ({url, fetched, license})."""
    out, seen = [], set()
    for mf in fetch_manifests:
        if not mf.exists():
            continue
        for s in json.loads(mf.read_text())["sources"]:
            if s["url"] in seen:
                continue
            seen.add(s["url"])
            out.append(
                {
                    "url": s["url"],
                    "fetched": s.get("downloaded", "")[:10],
                    "license": s.get("licence", "public domain"),
                }
            )
    return out


def write_layer(
    folder: Path,
    layer: str,
    site: str,
    version: str,
    bounds_lonlat: list[float],
    assets: dict[str, str],
    sources: list[dict],
    resolution_m: float | None = None,
    depends_on: list[str] | None = None,
    notes: str = "",
) -> dict:
    doc = {
        "layer": layer,
        "site": site,
        "version": version,
        "schema": layerjson.SCHEMA_VERSION,
        "owner": "B",
        "crs": BASE_CRS_CODE,
        "datum": DATUM_ID,
        "bounds_lonlat": [round(float(v), 6) for v in bounds_lonlat],
        "assets": assets,
        "depends_on": depends_on or [],
        "sources": sources,
        "validated": False,
        "built_with": "local (mars-sv-common " + __import__("common").__version__ + ")",
        "notes": notes,
    }
    if resolution_m:
        doc["resolution_m"] = float(resolution_m)
    layerjson.write(folder / "layer.json", doc)
    report = validate_layer(Target(str(folder)))
    doc["validated"] = report.ok
    layerjson.write(folder / "layer.json", doc)
    for line in report.failures:
        print(f"  VALIDATE FAIL {line}")
    print(f"  common.validate: {'ok' if report.ok else 'invalid'} {folder}")
    return doc
