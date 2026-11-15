"""L3 Rover poses — fetch raw inputs.

Downloads, for Perseverance (Jezero):
  * PLACES PDS4 bundle (localizations, orbital frame origins, ancillary, docs).
    The orbital basemap (3.9 GB .img + 0.5 GB .jpg + 3.1 GB .png browse) is skipped by default:
    orbital imagery is Person A's lane. The 324 MB PLACES DEM is kept because it is the
    surface PLACES heights refer to (used for the ~1 m cross-check).
  * MMGIS waypoints + traverse GeoJSON (cross-check against PLACES).

Usage:  python layers/l3_poses/fetch.py [--with-orbital-map] [--with-raw-xml]
Output: $MSV_DATA/raw/l3_poses/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_common.pds import download, human, list_dir, raw_dir, write_manifest  # noqa: E402

PLACES = "https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_rover_places/"
MMGIS = {
    "M20_waypoints.json": "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json",
    "M20_traverse.json": "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json",
}


def walk(url: str, skip_raw_xml: bool):
    for e in list_dir(url):
        if e.is_dir:
            if skip_raw_xml and e.name.endswith("_raw"):
                continue  # *_raw/ = per-site XML duplicates of the CSVs
            yield from walk(e.url, skip_raw_xml)
        else:
            yield e


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--with-orbital-map",
        action="store_true",
        help="also fetch the PLACES orbital map (~7.5 GB incl. browse)",
    )
    ap.add_argument(
        "--with-raw-xml", action="store_true", help="also fetch *_raw per-site XML files"
    )
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    out = raw_dir("l3_poses")
    sources = []

    files = list(walk(PLACES, not a.with_raw_xml))
    if not a.with_orbital_map:
        # orbital basemap + its 0.5/3.1 GB browse JPG/PNG: Person A's lane; keep the labels
        files = [
            e
            for e in files
            if not (e.name.startswith("m20_orbital_map.") and not e.name.endswith(".xml"))
        ]
    files = [e for e in files if e.name.lower() != "thumbs.db"]
    total = sum(e.size or 0 for e in files)
    print(f"PLACES: {len(files)} files, ~{human(total)}")
    for e in files:
        rel = e.url[len(PLACES) :]
        print(f"  {rel} ({human(e.size)})")
        if not a.dry_run:
            download(e.url, out / "places" / rel)
    sources.append(
        {
            "name": "Mars 2020 Rover PLACES bundle (PDS Geosciences)",
            "url": PLACES,
            "licence": "NASA PDS, public domain",
        }
    )

    for name, url in MMGIS.items():
        print(f"MMGIS: {name}")
        if not a.dry_run:
            download(url, out / "mmgis" / name)
        sources.append({"name": f"MMGIS {name}", "url": url, "licence": "NASA/JPL public release"})

    if not a.dry_run:
        write_manifest(out, sources)
    print("done ->", out)


if __name__ == "__main__":
    main()
