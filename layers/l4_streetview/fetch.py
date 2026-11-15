"""L4 Street view — fetch raw inputs for one or more rover stops.

A stop = (site, drive). For each stop this pulls from the PDS Imaging Node:
  * Navcam mosaics (mars2020_navcam_ops_mosaic): the 360 cylindrical panoramas the
    viewer needs (`CYL_S_*`: radiometric RAS, range RNG, per-pixel XYZ) plus the local
    orthorectified DEM + image (45 m extent, 1 cm) used to fuse with orbital terrain.
    Every product's PDS4 .xml label is kept (camera model, pose, frames).
  * Navcam meshes (mars2020_navcam_ops_mesh): .obj + .mtl + .png texture + label.
  * Optionally (--frames) the calibrated Navcam frames behind the panorama, for our own
    stitcher / CAHVORE camera models (plan fallback if Landform cannot be adapted).

Never mirrors whole archives: products are picked by stop code in the collection inventory.

Usage:
  python layers/l4_streetview/fetch.py                     # pilot stop (configs/rover/jezero.yaml)
  python layers/l4_streetview/fetch.py --stop 3 578        # any other site/drive
  python layers/l4_streetview/fetch.py --dry-run --all-mosaics
Output: $MSV_DATA/raw/l4_streetview/<stop_id>/{mosaic,mesh,frames}/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_common.pds import download, human, list_dir, raw_dir, write_manifest  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PDS = "https://pds-imaging.jpl.nasa.gov/data/mars2020/"
COLL = {
    "mosaic": ("mars2020_navcam_ops_mosaic", "mosaic"),
    "mesh": ("mars2020_navcam_ops_mesh", "mesh"),
    "frames": ("mars2020_navcam_ops_calibrated", "ncam"),
}

# Mosaic products wanted for a street-view node (the rest are stereo/anaglyph/slope/etc. variants).
MOSAIC_KEEP = [
    r"^N_[LR]RGB_\d{4}_(RAS|RNG|XYZ)_\d{7}_CYL_S_",  # 360 panoramas: image, range, XYZ
    r"^N_LRGB_\d{4}_DEM_\d{7}_ORR_L_45M01CM",  # local DEM, 45 m @ 1 cm
    r"^N_LRGB_\d{4}_RAS_\d{7}_ORR_R_45HIRES",  # matching ortho image
]


def stop_code(site: int, drive: int) -> str:
    return f"{site:03d}{drive:04d}"


def inventory(coll: str) -> list[str]:
    """Product names in a collection, cached in raw/l4_streetview/_index/."""
    idx = raw_dir("l4_streetview") / "_index"
    path = idx / f"{coll}.inv.csv"
    download(f"{PDS}{coll}/data/collection_data_inventory.csv", path, quiet=True)
    names = []
    for line in path.read_text().splitlines():
        parts = line.split(":")
        if len(parts) > 5:
            names.append(parts[5])
    return names


def sols_for_stop(coll: str, code: str) -> list[int]:
    sols = set()
    for n in inventory(coll):
        if code in n.replace("_", ""):
            m = re.search(r"_(\d{4})", n)
            if m:
                sols.add(int(m[1]))
    return sorted(sols)


def wanted(kind: str, name: str, code: str, all_mosaics: bool) -> bool:
    base = name.upper()
    if code not in base.replace("_", "")[:40]:
        return False
    if kind == "mosaic" and not all_mosaics:
        stem = re.sub(r"\.(IMG|XML)$", "", base)
        if base.endswith(".XML"):
            return True  # labels are small and carry camera model + pose
        return any(re.match(p, stem) for p in MOSAIC_KEEP)
    if kind == "frames":
        # Calibrated full-frame colour Navcam, radiometric (product type RAS), + labels.
        return bool(re.match(r"^N[LR]F_", base)) and base[23:26] == "RAS"
    return True


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--site-config", default=str(REPO / "configs/sites/jezero.yaml"))
    ap.add_argument(
        "--stop",
        nargs=2,
        type=int,
        metavar=("SITE", "DRIVE"),
        action="append",
        help="site and drive; repeatable. Default: pilot_stop from the site config",
    )
    ap.add_argument("--kinds", default="mosaic,mesh", help="comma list of mosaic,mesh,frames")
    ap.add_argument("--frames", action="store_true", help="shortcut: add calibrated frames")
    ap.add_argument(
        "--all-mosaics",
        action="store_true",
        help="every mosaic product, not just the street-view set",
    )
    ap.add_argument(
        "--max-gb", type=float, default=15.0, help="refuse to start above this many GB per stop"
    )
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.site_config))
    stops = a.stop or [[cfg["pilot_stop"]["site"], cfg["pilot_stop"]["drive"]]]
    kinds = a.kinds.split(",") + (["frames"] if a.frames else [])
    root = raw_dir("l4_streetview")

    for site, drive in stops:
        code = stop_code(site, drive)
        stop_id = f"{cfg['mission_prefix']}_site{site}_drive{drive}"
        print(f"== {stop_id}")
        plan = []
        for kind in kinds:
            coll, leaf = COLL[kind]
            sols = sols_for_stop(coll, code)
            for sol in sols:
                url = f"{PDS}{coll}/data/sol/{sol:05d}/ids/rdr/{leaf}/"
                try:
                    entries = list_dir(url)
                except Exception as e:  # some sols lack a given product dir
                    print(f"  skip {url}: {e}")
                    continue
                for e in entries:
                    if not e.is_dir and wanted(kind, e.name, code, a.all_mosaics):
                        plan.append((kind, sol, e))
        total = sum(e.size or 0 for _, _, e in plan)
        print(f"  {len(plan)} files, ~{human(total)}")
        for kind in kinds:
            sub = [(s, e) for k, s, e in plan if k == kind]
            print(
                f"   {kind}: {len(sub)} files, {human(sum(e.size or 0 for _, e in sub))}, "
                f"sols {sorted({s for s, _ in sub})}"
            )
        if a.dry_run:
            for k, _s, e in plan:
                if not e.name.upper().endswith(".XML"):
                    print(f"     {k} {e.name} {human(e.size)}")
            continue
        if total > a.max_gb * 1024**3:
            sys.exit(
                f"  {human(total)} exceeds --max-gb {a.max_gb}; narrow --kinds or raise the limit"
            )
        for i, (kind, _sol, e) in enumerate(plan, 1):
            print(f"  [{i}/{len(plan)}] {kind} {e.name} ({human(e.size)})", flush=True)
            download(e.url, root / stop_id / kind / e.name)
        write_manifest(
            root / stop_id,
            [
                {
                    "name": f"PDS {COLL[k][0]} for site {site} drive {drive}",
                    "url": f"{PDS}{COLL[k][0]}/",
                    "licence": "NASA/JPL-Caltech, public domain (PDS)",
                }
                for k in kinds
            ],
        )
    print("done ->", root)


if __name__ == "__main__":
    main()
