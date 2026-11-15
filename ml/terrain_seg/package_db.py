"""Package the patch vector database as a B-lane layer folder (proposal, needs a `contract` issue).

Layout (local mirror of rover/l9_terrain_db/<site>/vN/ in the bucket):
  vector_db.npz   embedding (N x 768, float16, L2-normalised MOMO patch tokens), label (int),
                  area (source area name), patch_id
  db.json         classes, model id, k, unknown threshold (cosine), counts, build parameters
  layer.json      schema 1.0, owner B, validated with common.validate

Usage: python ml/terrain_seg/package_db.py --version v1
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import DATA, PlacesSource  # noqa: E402

from common import layerjson  # noqa: E402
from common.crs import BASE_CRS_CODE, DATUM_ID  # noqa: E402
from common.validate import print_report, validate  # noqa: E402

SRC = DATA / "work/terrain_seg/vector_db_patch"
CLASSES = ["soil", "bedrock", "sand", "big_rock"]
MODEL = {
    "name": "MOMO ViT-B/16 (Kerner Lab, CVPR 2026)",
    "checkpoint": "vit-b-16/momo.pth",
    "url": "https://huggingface.co/Mirali33/MOMO",
    "license": "CC-BY-4.0",
    "tokens": "16 px patches of the 0.25 m ortho (4 x 4 m), position-debiased, "
    "centred per tile, L2-normalised",
}


def area_bounds(names: list[str]) -> list[float]:
    places = PlacesSource()
    lons, lats = [], []
    for n in names:
        w = np.load(DATA / "raw/places_map_window" / f"{n}.npz")
        e0, n0 = float(w["origin_e"]), float(w["origin_n"])
        side = w["b0"].shape[0] * float(w["res"])
        lo, la = places.to_lonlat(np.array([e0, e0 + side]), np.array([n0 - side, n0]))
        lons += list(lo)
        lats += list(la)
    return [min(lons), min(lats), max(lons), max(lats)]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--version", default="v1")
    ap.add_argument("--site", default="jezero")
    a = ap.parse_args()
    out = DATA / "rover/l9_terrain_db" / a.site / a.version
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(SRC / "vector_db_patch.npz", out / "vector_db.npz")
    summ = json.loads((SRC / "summary.json").read_text())
    db = np.load(out / "vector_db.npz")
    areas = sorted(set(db["area"].tolist()))
    meta = {
        "classes": CLASSES,
        "model": MODEL,
        "k": summ["k"],
        "unknown_threshold_cosine": summ["unknown_threshold_cosine"],
        "counts": summ["database"],
        "areas": areas,
        "evidence": "AI4Mars SegFormer on Perseverance Navcam, ray-cast on the PLACES DEM",
        "leave_one_area_out": summ["leave_one_area_out"],
    }
    (out / "db.json").write_text(json.dumps(meta, indent=2))
    doc = {
        "layer": "l9_terrain_db",
        "site": a.site,
        "version": a.version,
        "schema": layerjson.SCHEMA_VERSION,
        "owner": "B",
        "crs": BASE_CRS_CODE,
        "datum": DATUM_ID,
        "bounds_lonlat": area_bounds(areas),
        "assets": {"vector_db": "vector_db.npz", "meta": "db.json"},
        "depends_on": ["rover/l3_poses/jezero/v1"],
        "sources": [
            {
                "url": "https://huggingface.co/datasets/hassanjbara/AI4MARS",
                "fetched": str(date.today()),
                "license": "CC0-1.0",
            },
            {"url": MODEL["url"], "fetched": str(date.today()), "license": MODEL["license"]},
        ],
        "validated": True,
        "built_with": "ml/terrain_seg/package_db.py",
        "notes": "Proposal: layer name and asset layout need a `contract` issue.",
    }
    layerjson.write(out / "layer.json", doc)
    print_report(validate(str(out)))
    print("->", out, meta["counts"])


if __name__ == "__main__":
    main()
