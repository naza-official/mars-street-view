"""Cut ~30 review fragments from a segmented stop panorama for a human to grade.

Fragments are stratified by range band (0-5, 5-15, 15-30 m) and by predicted dominant class, so the
near field and the far field both get checked. Each PNG shows the original crop and the prediction
side by side; review.csv has one row per fragment with empty columns for the verdict.

Input : $MSV_DATA/work/terrain_seg/<stop>/classes.png (from infer_m2020.py) + the stop mosaics
Output: <repo>/review/terrain_seg/<stop>/  (git-ignored)

Usage: python ml/terrain_seg/review_crops.py --stop m2020_site1_drive52 --n 30
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from infer_m2020 import (  # noqa: E402
    BANDS_M,
    COLOURS,
    find,
    gray_panorama,
    range_on_ras_grid,
)
from train import CLASSES, IGNORE  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
UA = {"soil": "ґрунт", "bedrock": "скеля", "sand": "пісок", "big_rock": "великий камінь"}
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


TILE_BY_BAND = {"0-5m": 200, "5-15m": 140, "15-30m": 80}
VIEW_PX = 600


def candidates(cls: np.ndarray, rng: np.ndarray, tile: int, min_labelled: float,
               band_only: str | None = None) -> list[dict]:
    out = []
    h, w = cls.shape
    for y in range(0, h - tile, tile // 2):
        for x in range(0, w - tile, tile // 2):
            c = cls[y:y + tile, x:x + tile]
            lab = c != IGNORE
            if lab.mean() < min_labelled:
                continue
            r = float(np.nanmedian(rng[y:y + tile, x:x + tile][lab]))
            band = next((f"{lo}-{hi}m" for lo, hi in BANDS_M if lo <= r < hi), None)
            if band is None or (band_only and band != band_only):
                continue
            counts = np.bincount(c[lab], minlength=len(CLASSES))[: len(CLASSES)]
            share = counts / counts.sum()
            out.append({"x": x, "y": y, "tile": tile, "range_m": r, "band": band,
                        "dominant": CLASSES[int(share.argmax())], "share": share})
    return out


def pick(cands: list[dict], n: int, seed: int = 0) -> list[dict]:
    rnd = random.Random(seed)
    per_band = n // len(BANDS_M)
    chosen = []
    for lo, hi in BANDS_M:
        band = [c for c in cands if c["band"] == f"{lo}-{hi}m"]
        by_cls = {k: [c for c in band if c["dominant"] == k] for k in CLASSES}
        for v in by_cls.values():
            rnd.shuffle(v)
        taken = []
        while len(taken) < per_band and any(by_cls.values()):
            for k in CLASSES:
                if by_cls[k] and len(taken) < per_band:
                    c = by_cls[k].pop()
                    gap = 1.5 * c["tile"]
                    if all(abs(c["x"] - t["x"]) + abs(c["y"] - t["y"]) > gap for t in taken):
                        taken.append(c)
        chosen += taken
    return chosen


def render(gray: np.ndarray, cls: np.ndarray, c: dict, idx: int) -> Image.Image:
    y, x, tile = c["y"], c["x"], c["tile"]
    g = gray[y:y + tile, x:x + tile]
    k = cls[y:y + tile, x:x + tile]
    rgb = np.repeat(g[..., None], 3, axis=2).astype(np.float32)
    lab = k != IGNORE
    ov = rgb.copy()
    ov[lab] = 0.45 * rgb[lab] + 0.55 * COLOURS[k[lab]]
    v = VIEW_PX
    left = Image.fromarray(rgb.astype(np.uint8)).resize((v, v), Image.LANCZOS)
    right = Image.fromarray(ov.astype(np.uint8)).resize((v, v), Image.NEAREST)
    pad, head, foot = 10, 44, 40
    W = 2 * v + 3 * pad
    H = v + head + foot + 2 * pad
    im = Image.new("RGB", (W, H), (24, 24, 24))
    im.paste(left, (pad, head + pad))
    im.paste(right, (2 * pad + v, head + pad))
    d = ImageDraw.Draw(im)
    shares = ", ".join(f"{UA[n]} {v:.0%}" for n, v in zip(CLASSES, c["share"], strict=True)
                       if v >= 0.01)
    d.text((pad, 8), f"#{idx:02d}   ~{c['range_m']:.1f} м   передбачено: {UA[c['dominant']]}"
           f"   (плитка {tile} px ≈ {tile / 12.9:.0f}°)",
           font=font(22), fill=(255, 255, 255))
    d.text((pad, H - foot + 6), shares, font=font(18), fill=(210, 210, 210))
    x0 = W - 4 * 150 - pad
    for i, n in enumerate(CLASSES):
        d.rectangle([x0 + i * 150, H - foot + 8, x0 + i * 150 + 18, H - foot + 26],
                    fill=tuple(int(v) for v in COLOURS[i]))
        d.text((x0 + i * 150 + 24, H - foot + 6), UA[n], font=font(16), fill=(230, 230, 230))
    return im


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stop", default="m2020_site1_drive52")
    ap.add_argument("--data", type=Path, default=Path.home() / "mars-data")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", type=Path, default=ROOT / "review" / "terrain_seg")
    a = ap.parse_args()

    mosaic = a.data / "raw/l4_streetview" / a.stop / "mosaic"
    cls = np.asarray(Image.open(a.data / "work/terrain_seg" / a.stop / "classes.png"))
    ras = find(mosaic, "RAS")
    gray = gray_panorama(ras)
    rng = range_on_ras_grid(find(mosaic, "RNG"), ras, gray.shape)
    cands = []
    for band, tile in TILE_BY_BAND.items():
        cands += candidates(cls, rng, tile, 0.6, band)
    chosen = pick(cands, a.n)

    out = a.out / a.stop
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, c in enumerate(sorted(chosen, key=lambda c: c["range_m"]), 1):
        name = f"{i:02d}_{c['band']}_{c['dominant']}.png"
        render(gray, cls, c, i).save(out / name)
        rows.append({"id": i, "file": name, "range_m": round(c["range_m"], 1), "band": c["band"],
                     "predicted": c["dominant"],
                     **{f"share_{n}": round(float(v), 3)
                        for n, v in zip(CLASSES, c["share"], strict=True)},
                     "pano_x": c["x"], "pano_y": c["y"],
                     "verdict (ok / partly / wrong)": "", "true_class": "", "comment": ""})
    with open(out / "review.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} fragments -> {out}")
    for b in {r['band'] for r in rows}:
        sub = [r["predicted"] for r in rows if r["band"] == b]
        print(f"  {b}: {len(sub)}  {dict((k, sub.count(k)) for k in set(sub))}")


if __name__ == "__main__":
    main()
