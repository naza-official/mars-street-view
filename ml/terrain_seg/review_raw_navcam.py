"""Grade the terrain model on mid-range Perseverance Navcam-Right views from the raw-images site.

Picks ~N wide views spread over the mission (one per sampled page of the sol-sorted feed): Navcam
right, scaleFactor 2 or 4 (>= ~48 deg field of view), mast elevation -30..0 deg, mast azimuth
within 90 deg of rover-forward (mid-range terrain plus some horizon, not the deck). Each image is
resampled to the training angular scale (~1.6 mrad/px) and segmented; the sky is masked (smooth
region touching the top edge). A side-by-side
review PNG is written, plus review.csv with empty verdict columns.

Downloads: $MSV_DATA/raw/navcam_right_review/*.jpg  (raw-images "large" JPEG, 1200 px wide)
Output   : <repo>/review/terrain_seg/navcam_right/  (git-ignored)

Usage: python ml/terrain_seg/review_raw_navcam.py --n 30
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import requests
import torch
import torch.nn.functional as F
from PIL import Image, ImageDraw
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from infer_m2020 import COLOURS, TRAIN_MRAD_PER_PX, predict  # noqa: E402
from review_crops import UA, font  # noqa: E402
from train import CLASSES, IGNORE  # noqa: E402
from transformers import SegformerForSemanticSegmentation  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
API = ("https://mars.nasa.gov/rss/api/?feed=raw_images&category=mars2020&feedtype=json"
       "&num=100&order=sol+desc&search=NAVCAM_RIGHT&page={page}")
NATIVE_MRAD = 0.327
UA_AGENT = {"User-Agent": "mars-street-view-review/0.1 (research)"}


def wide_view(im: dict, el_range: tuple[float, float]) -> bool:
    e = im.get("extended", {})
    try:
        sf, el, az = int(e["scaleFactor"]), float(e["mastEl"]), float(e["mastAz"])
        w = int(e["dimension"].strip("()").split(",")[0])
    except (KeyError, ValueError):
        return False
    forward = abs(((az + 180) % 360) - 180) <= 90
    return sf >= 2 and w * sf >= 2560 and el_range[0] <= el <= el_range[1] and forward


def sample_images(n: int, pages: int, el_range: tuple[float, float],
                  exclude: set[int] | None = None, offset: float = 0.0) -> list[dict]:
    exclude = exclude or set()
    s = requests.Session()
    s.headers.update(UA_AGENT)
    total = s.get(API.format(page=0), timeout=60).json()["total_results"]
    last = total // 100
    picks, seen_sols = [], set()
    step = last / max(pages - 1, 1)
    for p in np.clip(np.linspace(0, last, pages) + offset * step, 0, last).astype(int):
        try:
            ims = s.get(API.format(page=int(p)), timeout=60).json()["images"]
        except (requests.RequestException, ValueError):
            continue
        for im in ims:
            if wide_view(im, el_range) and im["sol"] not in seen_sols | exclude:
                picks.append(im)
                seen_sols.add(im["sol"])
                break
        time.sleep(0.3)
    if len(picks) > n:
        picks = [picks[i] for i in np.linspace(0, len(picks) - 1, n).round().astype(int)]
    return picks


def sky_mask(gray: np.ndarray, thr: float = 4.0) -> np.ndarray:
    g = gray.astype(np.float32)
    mean = ndimage.uniform_filter(g, 9)
    std = np.sqrt(np.maximum(ndimage.uniform_filter(g * g, 9) - mean * mean, 0))
    smooth = std < thr
    lab, _ = ndimage.label(smooth)
    top = set(np.unique(lab[0])) - {0}
    sky = np.isin(lab, list(top))
    return ndimage.binary_dilation(sky, iterations=3)


def render(rgb: np.ndarray, cls: np.ndarray, idx: int, im: dict,
           note: str = "") -> tuple[Image.Image, np.ndarray]:
    ov = rgb.astype(np.float32).copy()
    lab = cls != IGNORE
    ov[lab] = 0.45 * ov[lab] + 0.55 * COLOURS[cls[lab]]
    h, w = cls.shape
    v = 640
    vh = int(h * v / w)
    pad, head, foot = 10, 44, 40
    W, H = 2 * v + 3 * pad, vh + head + foot + 2 * pad
    out = Image.new("RGB", (W, H), (24, 24, 24))
    out.paste(Image.fromarray(rgb).resize((v, vh), Image.LANCZOS), (pad, head + pad))
    out.paste(Image.fromarray(ov.astype(np.uint8)).resize((v, vh), Image.NEAREST),
              (2 * pad + v, head + pad))
    d = ImageDraw.Draw(out)
    counts = np.bincount(cls[lab], minlength=len(CLASSES))[: len(CLASSES)]
    share = counts / max(counts.sum(), 1)
    e = im["extended"]
    d.text((pad, 8), f"#{idx:02d}   sol {im['sol']}   mast el {float(e['mastEl']):.0f}°   "
           f"передбачено: {UA[CLASSES[int(share.argmax())]]}   {note}", font=font(22),
           fill=(255, 255, 255))
    d.text((pad, H - foot + 6),
           ", ".join(f"{UA[n]} {s:.0%}" for n, s in zip(CLASSES, share, strict=True) if s >= 0.01),
           font=font(18), fill=(210, 210, 210))
    x0 = W - 4 * 150 - pad
    for i, n in enumerate(CLASSES):
        d.rectangle([x0 + i * 150, H - foot + 8, x0 + i * 150 + 18, H - foot + 26],
                    fill=tuple(int(c) for c in COLOURS[i]))
        d.text((x0 + i * 150 + 24, H - foot + 6), UA[n], font=font(16), fill=(230, 230, 230))
    return out, share


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--pages", type=int, default=60, help="feed pages sampled across the mission")
    ap.add_argument("--el-min", type=float, default=-30.0)
    ap.add_argument("--el-max", type=float, default=0.0)
    ap.add_argument("--model", type=Path,
                    default=Path.home() / "mars-data/models/ai4mars_segformer_b2")
    ap.add_argument("--raw", type=Path, default=Path.home() / "mars-data/raw/navcam_right_review")
    ap.add_argument("--out", type=Path, default=ROOT / "review/terrain_seg/navcam_right")
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--exclude-sols", type=int, nargs="*", default=[],
                    help="sols to skip (e.g. arm in view, sun glare); the next view is taken")
    a = ap.parse_args()
    a.raw.mkdir(parents=True, exist_ok=True)
    a.out.mkdir(parents=True, exist_ok=True)

    picks = sample_images(a.n, a.pages, (a.el_min, a.el_max), set(a.exclude_sols))
    print(f"{len(picks)} wide Navcam-Right views picked, sols "
          f"{sorted(p['sol'] for p in picks)}")
    (a.raw / "picks.json").write_text(json.dumps(picks, indent=1))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = SegformerForSemanticSegmentation.from_pretrained(a.model).to(device).eval()
    s = requests.Session()
    s.headers.update(UA_AGENT)
    rows = []
    for i, im in enumerate(sorted(picks, key=lambda p: p["sol"]), 1):
        url = im["image_files"]["large"]
        f = a.raw / url.rsplit("/", 1)[-1]
        if not f.exists():
            f.write_bytes(s.get(url, timeout=120).content)
        rgb = np.asarray(Image.open(f).convert("RGB"))
        gray = np.asarray(Image.fromarray(rgb).convert("L"))
        e = im["extended"]
        dim_w = int(e["dimension"].strip("()").split(",")[0])
        mrad = NATIVE_MRAD * int(e["scaleFactor"]) * dim_w / rgb.shape[1]
        sc = mrad / TRAIN_MRAD_PER_PX
        small = np.asarray(Image.fromarray(gray).resize(
            (int(gray.shape[1] * sc), int(gray.shape[0] * sc)), Image.BILINEAR))
        prob = predict(model, small, device)
        prob = F.interpolate(torch.from_numpy(prob)[None], size=gray.shape, mode="bilinear",
                             align_corners=False)[0].numpy()
        cls = prob.argmax(0).astype(np.uint8)
        cls[(prob.max(0) < a.min_conf) | sky_mask(gray)] = IGNORE
        name = f"{i:02d}_sol{im['sol']:04d}.png"
        panel, share = render(rgb, cls, i, im)
        panel.save(a.out / name)
        rows.append({"id": i, "file": name, "sol": im["sol"], "imageid": im["imageid"],
                     "mast_el_deg": round(float(e["mastEl"]), 1), "mrad_per_px": round(mrad, 2),
                     "predicted": CLASSES[int(share.argmax())],
                     **{f"share_{n}": round(float(v), 3)
                        for n, v in zip(CLASSES, share, strict=True)},
                     "image_url": url,
                     "verdict (ok / partly / wrong)": "", "true_class": "", "comment": ""})
        print(f"  {name}: {rows[-1]['predicted']}  {mrad:.2f} mrad/px", flush=True)
    with open(a.out / "review.csv", "w", newline="", encoding="utf-8-sig") as fo:
        w = csv.DictWriter(fo, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
