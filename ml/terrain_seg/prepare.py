"""AI4Mars parquet shards -> 512 px PNG pairs for training (one pass, low memory).

Labels: 0 soil, 1 bedrock, 2 sand, 3 big rock, 255 ignore. Rover pixels (rover_mask == 1) are set
to 255 so the model is never scored on the rover body.

Usage: python ml/terrain_seg/prepare.py  (defaults: ~/mars-data/raw/ai4mars -> work/ai4mars512)
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from PIL import Image

SIZE = 512
IGNORE = 255
CLASSES = ["soil", "bedrock", "sand", "big_rock"]


def decode(cell: dict) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(cell["bytes"])))


def label_from_row(row: dict) -> np.ndarray:
    lab = decode(row["label_mask"]).copy()
    lab[lab > 3] = IGNORE
    if row.get("rover_mask") and row["rover_mask"].get("bytes"):
        rov = decode(row["rover_mask"])
        if rov.shape == lab.shape:
            lab[rov > 0] = IGNORE
    return lab


def to_gray(a: np.ndarray) -> np.ndarray:
    return a if a.ndim == 2 else np.asarray(Image.fromarray(a).convert("L"))


def write_pair(img: np.ndarray, lab: np.ndarray, out: Path, stem: str) -> None:
    Image.fromarray(to_gray(img)).resize((SIZE, SIZE), Image.BILINEAR).save(out / f"{stem}.jpg",
                                                                            quality=95)
    Image.fromarray(lab).resize((SIZE, SIZE), Image.NEAREST).save(out / f"{stem}.png")


def convert(files: list[Path], out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in files:
        pf = pq.ParquetFile(f)
        for rg in range(pf.num_row_groups):
            for row in pf.read_row_group(rg).to_pylist():
                if not row.get("has_labels"):
                    continue
                stem = Path(row["image"]["path"]).stem
                if (out / f"{stem}.png").exists():
                    n += 1
                    continue
                write_pair(decode(row["image"]), label_from_row(row), out, stem)
                n += 1
        print(f"  {f.name}: {n} pairs so far", flush=True)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=Path.home() / "mars-data/raw/ai4mars")
    ap.add_argument("--dst", type=Path, default=Path.home() / "mars-data/work/ai4mars512")
    a = ap.parse_args()
    splits = {"train": sorted(a.src.glob("train-*.parquet"))}
    for t in ("test_min1", "test_min2", "test_min3"):
        if (a.src / f"{t}.parquet").exists():
            splits[t] = [a.src / f"{t}.parquet"]
    for name, files in splits.items():
        print(f"{name}: {len(files)} shard(s)")
        print(f"{name}: {convert(files, a.dst / name)} pairs")


if __name__ == "__main__":
    main()
