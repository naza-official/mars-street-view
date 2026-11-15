import json
import sys
from pathlib import Path

from common import layerjson
from common.crs import BASE_CRS_CODE, DATUM_ID, METRES_PER_DEGREE
from common.terrain import TileKey
from layers.l1_global import ingest, layer, region


def main(layer_dir: Path, root: str, check_json: str) -> int:
    extent = region.root_extent(TileKey(*map(int, root.split("/"))))
    stats = json.loads((layer_dir.parent / f"{layer_dir.name}.stats.json").read_text())
    doc = {
        "layer": "l1_global", "site": "jezero_poc", "version": "v1",
        "schema": layerjson.SCHEMA_VERSION, "owner": "A", "crs": BASE_CRS_CODE,
        "datum": DATUM_ID,
        "bounds_lonlat": [extent.west, extent.south, extent.east, extent.north],
        "resolution_m": round(METRES_PER_DEGREE / ingest.MOLA.ppd, 3),
        "assets": layer.ASSETS, "depends_on": [], "sources": layer.sources("2026-11-15"),
        "validated": True, "built_with": "ghcr.io/naza-official/mars-sv:v0.4.2 + poc region.py",
        "notes": (f"Local PoC: L1 subtree {root} only, not published. dem_geoid vs PDS areoid: "
                  f"{check_json}. {stats['tiles']} tiles, {stats['bytes']} bytes."),
    }
    layerjson.write(layer_dir / "layer.json", doc)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
