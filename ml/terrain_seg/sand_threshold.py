"""Inspect sand-vs-soil confidence and choose a sand bias without retraining.

Decision rule with a bias b <= 0 on the sand logit: a pixel is sand only if
    log p_sand + b > log p_other   for every other class,
i.e. sand must beat soil by a factor exp(-b) (b = -1 -> 2.7x more likely, b = -2 -> 7.4x).

What it does
  1. For the Navcam-Right review images (picks.json from review_raw_navcam.py) it recomputes the
     class probabilities and writes, next to the review folder, per image:
         <name>_classes.png   class index with bias (0 soil 1 bedrock 2 sand 3 big rock 255 none)
         <name>_color.png     the same in colour
         <name>_p_sand.png, <name>_p_soil.png   probabilities x 255
         <name>_margin.png    p_sand - p_soil mapped to 0..255 (128 = equal)
     and probs.npz with the full probability stacks.
  2. Prints, per image, how sure the model is on pixels it calls sand, and how many of them would
     flip to another class for each candidate bias.
  3. Sweeps the same biases on the AI4Mars expert test split (test_min3) and prints sand/soil
     precision, recall and IoU, so the cost of a bias on labelled data is visible.

Usage:
  python ml/terrain_seg/sand_threshold.py                 # stats + sweep, masks without bias
  python ml/terrain_seg/sand_threshold.py --bias -1.5     # masks written with the chosen bias
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from infer_m2020 import COLOURS, TRAIN_MRAD_PER_PX, predict  # noqa: E402
from review_raw_navcam import NATIVE_MRAD, sky_mask  # noqa: E402
from train import CLASSES, IGNORE, PairDir, logits_full, stems_in  # noqa: E402
from transformers import SegformerForSemanticSegmentation  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SOIL, BEDROCK, SAND, ROCK = range(4)
BIASES = [0.0, -0.5, -1.0, -1.5, -2.0, -2.5, -3.0]


def decide(prob: np.ndarray, bias: float) -> np.ndarray:
    logp = np.log(np.clip(prob, 1e-8, 1))
    logp[SAND] += bias
    return logp.argmax(0).astype(np.uint8)


def image_probs(model, device, f: Path, im: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rgb = np.asarray(Image.open(f).convert("RGB"))
    gray = np.asarray(Image.fromarray(rgb).convert("L"))
    e = im["extended"]
    dim_w = int(e["dimension"].strip("()").split(",")[0])
    sc = NATIVE_MRAD * int(e["scaleFactor"]) * dim_w / rgb.shape[1] / TRAIN_MRAD_PER_PX
    small = np.asarray(Image.fromarray(gray).resize(
        (int(gray.shape[1] * sc), int(gray.shape[0] * sc)), Image.BILINEAR))
    prob = predict(model, small, device)
    prob = F.interpolate(torch.from_numpy(prob)[None], size=gray.shape, mode="bilinear",
                         align_corners=False)[0].numpy()
    return rgb, prob, ~sky_mask(gray)


def save_masks(out: Path, name: str, prob: np.ndarray, valid: np.ndarray, bias: float,
               min_conf: float) -> np.ndarray:
    cls = decide(prob, bias)
    cls[~valid | (prob.max(0) < min_conf)] = IGNORE
    Image.fromarray(cls).save(out / f"{name}_classes.png")
    col = np.zeros((*cls.shape, 3), np.uint8)
    lab = cls != IGNORE
    col[lab] = COLOURS[cls[lab]]
    Image.fromarray(col).save(out / f"{name}_color.png")
    for k, c in (("sand", SAND), ("soil", SOIL)):
        Image.fromarray((prob[c] * 255).astype(np.uint8)).save(out / f"{name}_p_{k}.png")
    margin = ((prob[SAND] - prob[SOIL] + 1) * 127.5).astype(np.uint8)
    Image.fromarray(margin).save(out / f"{name}_margin.png")
    return cls


def image_report(name: str, prob: np.ndarray, valid: np.ndarray) -> dict:
    base = decide(prob, 0.0)
    sand = valid & (base == SAND)
    n_valid, n_sand = int(valid.sum()), int(sand.sum())
    row = {"name": name, "sand_share": n_sand / max(n_valid, 1)}
    if n_sand:
        ps, po = prob[SAND][sand], prob[SOIL][sand]
        ratio = np.log(np.clip(ps, 1e-8, 1)) - np.log(np.clip(po, 1e-8, 1))
        row |= {"p_sand_med": float(np.median(ps)), "p_soil_med": float(np.median(po)),
                "logratio_p10": float(np.percentile(ratio, 10)),
                "logratio_p50": float(np.median(ratio)),
                "logratio_p90": float(np.percentile(ratio, 90))}
        for b in BIASES[1:]:
            row[f"flip@{b}"] = float((decide(prob, b)[sand] != SAND).mean())
    return row


def print_images(rows: list[dict]) -> None:
    head = f"{'image':16s} {'sand%':>6s} {'p_sand':>6s} {'p_soil':>6s}"
    head += "  log(p_sand/p_soil) p10/p50/p90"
    head += "   flipped sand share at bias " + " ".join(f"{b:>5}" for b in BIASES[1:])
    print(head)
    for r in rows:
        if "p_sand_med" not in r:
            print(f"{r['name']:16s} {r['sand_share']:6.1%}   (no sand predicted)")
            continue
        flips = " ".join(f"{r[f'flip@{b}']:5.0%}" for b in BIASES[1:])
        print(f"{r['name']:16s} {r['sand_share']:6.1%} "
              f"{r['p_sand_med']:6.2f} {r['p_soil_med']:6.2f}"
              f"  {r['logratio_p10']:6.2f} {r['logratio_p50']:6.2f} {r['logratio_p90']:6.2f}"
              f"{'':30s}{flips}")


def text_hist(values: np.ndarray, edges: np.ndarray, width: int = 50) -> None:
    h, _ = np.histogram(values, edges)
    top = max(h.max(), 1)
    for lo, hi, n in zip(edges[:-1], edges[1:], h, strict=True):
        print(f"  {lo:5.1f}..{hi:5.1f} {'#' * int(width * n / top):{width}s} {n / h.sum():5.1%}")


@torch.no_grad()
def test_sweep(model, device, test_dir: Path) -> None:
    ds = PairDir(test_dir, stems_in(test_dir), False)
    tp = {b: np.zeros(4) for b in BIASES}
    fp = {b: np.zeros(4) for b in BIASES}
    fn = {b: np.zeros(4) for b in BIASES}
    for i in range(len(ds)):
        x, y = ds[i]
        with torch.autocast("cuda", dtype=torch.float16, enabled=device.type == "cuda"):
            lg = logits_full(model, x[None].to(device))[0].float()
        logp = lg.log_softmax(0).cpu().numpy()
        y = y.numpy()
        valid = y != IGNORE
        for b in BIASES:
            lp = logp.copy()
            lp[SAND] += b
            p = lp.argmax(0)
            for c in range(4):
                pc, yc = (p == c) & valid, (y == c) & valid
                tp[b][c] += (pc & yc).sum()
                fp[b][c] += (pc & ~yc).sum()
                fn[b][c] += (~pc & yc).sum()
    print("\nAI4Mars test_min3 (expert labels): effect of the sand bias")
    print(f"{'bias':>5s} {'ratio':>6s} | {'sand P':>6s} {'sand R':>6s} {'sand IoU':>8s} | "
          f"{'soil P':>6s} {'soil R':>6s} {'soil IoU':>8s} | {'mIoU':>5s}")
    for b in BIASES:
        iou = tp[b] / np.maximum(tp[b] + fp[b] + fn[b], 1)
        prec = tp[b] / np.maximum(tp[b] + fp[b], 1)
        rec = tp[b] / np.maximum(tp[b] + fn[b], 1)
        print(f"{b:5.1f} {np.exp(-b):5.1f}x | "
              f"{prec[SAND]:6.3f} {rec[SAND]:6.3f} {iou[SAND]:8.3f} | "
              f"{prec[SOIL]:6.3f} {rec[SOIL]:6.3f} {iou[SOIL]:8.3f} | {iou.mean():5.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bias", type=float, default=0.0, help="sand logit bias used for saved masks")
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--model", type=Path,
                    default=Path.home() / "mars-data/models/ai4mars_segformer_b2")
    ap.add_argument("--raw", type=Path, default=Path.home() / "mars-data/raw/navcam_right_review")
    ap.add_argument("--out", type=Path, default=ROOT / "review/terrain_seg/navcam_right_masks")
    ap.add_argument("--test", type=Path,
                    default=Path.home() / "mars-data/work/ai4mars512/test_min3")
    ap.add_argument("--no-test", action="store_true", help="skip the AI4Mars test sweep")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = SegformerForSemanticSegmentation.from_pretrained(a.model).to(device).eval()

    picks = sorted(json.loads((a.raw / "picks.json").read_text()), key=lambda p: p["sol"])
    rows, ratios, stacks = [], [], {}
    for i, im in enumerate(picks, 1):
        f = a.raw / im["image_files"]["large"].rsplit("/", 1)[-1]
        name = f"{i:02d}_sol{im['sol']:04d}"
        _, prob, valid = image_probs(model, device, f, im)
        save_masks(a.out, name, prob, valid, a.bias, a.min_conf)
        stacks[name] = (prob * 255).astype(np.uint8)
        rows.append(image_report(name, prob, valid))
        sand = valid & (decide(prob, 0.0) == SAND)
        if sand.any():
            r = np.log(np.clip(prob[SAND][sand], 1e-8, 1))
            r = r - np.log(np.clip(prob[SOIL][sand], 1e-8, 1))
            ratios.append(r[:: max(1, r.size // 20000)])
    np.savez_compressed(a.out / "probs.npz", classes=np.array(CLASSES), **stacks)

    print(f"masks -> {a.out}  (bias {a.bias})\n")
    print_images(rows)
    if ratios:
        allr = np.concatenate(ratios)
        print("\nlog(p_sand / p_soil) on pixels called sand (all images); "
              "a bias b flips everything below -b")
        text_hist(allr, np.array([0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 6, 10, 20]))
        for b in BIASES[1:]:
            print(f"  bias {b:4.1f} (sand must be {np.exp(-b):4.1f}x soil): "
                  f"{(allr < -b).mean():5.1%} of predicted sand flips")
    if not a.no_test:
        test_sweep(model, device, a.test)
    (a.out / "stats.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
