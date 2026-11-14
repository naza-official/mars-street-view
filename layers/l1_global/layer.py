import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import layerjson
from common.crs import BASE_CRS_CODE, DATUM_ID, METRES_PER_DEGREE
from layers.l1_global import ingest, tiles

LAYER, SITE, VERSION = "l1_global", "global", "v1"
ASSETS = {"dtm": "dtm.tif", "ortho": "ortho.tif", "tileset": "tileset.json"}
SHIFT_LIMIT_M = 232.0


def sources(fetched: str) -> list[dict[str, str]]:
    return [{"url": s.url, "fetched": fetched, "license": ingest.LICENSE}
            for s in (ingest.MOLA, ingest.MDIM)] + [
        {"url": ingest.AREOID_URL, "fetched": fetched, "license": "public domain (NASA PDS)"}]


def notes(conversion: dict, registration: dict, geoid: dict, stats: dict) -> str:
    return (
        f"MDIM datum-conversion shift at Jezero (77.5E 18.4N), measured: "
        f"{conversion['east_m']} m E, {conversion['north_m']} m N "
        f"(|{conversion['magnitude_m']}| m, limit {SHIFT_LIMIT_M:.0f} m; D-008). "
        f"MDIM 2.1 vs MOLA image registration at Jezero: {registration['east_m']} m E, "
        f"{registration['north_m']} m N (NCC {registration['score']}); a source limitation, "
        f"not corrected in v1 (D-008). "
        f"Heights: MOLA areoid -> sphere with ASP dem_geoid; vs PDS areoid mean "
        f"{geoid['mean_m']:.3f} m, max {geoid['max_abs_m']:.3f} m over {geoid['n']} px. "
        f"Tiles: 3D Tiles 1.1 GLB, {stats['tiles']} tiles, {stats['bytes'] / 1e9:.2f} GB, "
        f"levels 0-{tiles.LEAF_LEVEL}, leaf TIN error {stats.get('leaf_error_m', '?')} m, "
        f"KHR_mesh_quantization, MDIM JPEG textures {tiles.TEXTURE_PX} px, external "
        f"tilesets from level {tiles.SPLIT_LEVEL}."
    )


def layer_doc(conversion: dict, registration: dict, geoid: dict, stats: dict,
              built_with: str, fetched: str) -> dict[str, Any]:
    return {
        "layer": LAYER, "site": SITE, "version": VERSION, "schema": layerjson.SCHEMA_VERSION,
        "owner": "A", "crs": BASE_CRS_CODE, "datum": DATUM_ID,
        "bounds_lonlat": [0.0, -90.0, 360.0, 90.0],
        "resolution_m": round(METRES_PER_DEGREE / ingest.MOLA.ppd, 3),
        "assets": ASSETS, "depends_on": [], "sources": sources(fetched), "validated": True,
        "built_with": built_with, "notes": notes(conversion, registration, geoid, stats),
    }


def check_shift(conversion: dict) -> None:
    if conversion["magnitude_m"] >= SHIFT_LIMIT_M:
        raise ValueError(f"MDIM conversion shift {conversion['magnitude_m']} m >= limit")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m layers.l1_global.layer")
    parser.add_argument("layer_dir", type=Path)
    parser.add_argument("--conversion", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--geoid", type=Path, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--built-with", required=True)
    parser.add_argument("--fetched", required=True)
    args = parser.parse_args(argv)
    conversion = read_json(args.conversion)
    check_shift(conversion)
    doc = layer_doc(conversion, read_json(args.registration), read_json(args.geoid),
                    read_json(args.stats), args.built_with, args.fetched)
    print(f"wrote {layerjson.write(args.layer_dir / 'layer.json', doc)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
