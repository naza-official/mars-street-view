"""Photo report + per-frame class probabilities for Navcam-Right views, with per-class biases.

Run it yourself as often as you like: the model runs once per image, probabilities are cached, and
every later run with different biases only re-draws the report (seconds).

Decision rule: class = argmax(log p_c + bias_c). A negative bias makes a class harder to win
(sand=-1.5 -> sand must be e^1.5 = 4.5x as likely as the runner-up), a positive one easier.
Per-class minimum confidence (--min-class-conf sand=0.55): a pixel keeps class c only if p_c >= the
threshold; otherwise it falls back to the next most likely class (--fallback next, default) or stays
uncoloured (--fallback none).

Sets
  dev      the 30 views already reviewed (picks.json from review_raw_navcam.py)
  holdout  30 other views spread over the mission, disjoint by sol from dev; built on first use.
           Keep it for the final check only.

Output: <repo>/review/terrain_seg/<set>_<bias tag>/  NN_solXXXX.png + review.csv (git-ignored)
Console: per frame, mean probability of each class over terrain pixels, and the class shares after
         biases and the confidence cut.

Examples
  python ml/terrain_seg/report.py
  python ml/terrain_seg/report.py --bias sand=-1.5
  python ml/terrain_seg/report.py --bias sand=-1.5 bedrock=0.3 --min-conf 0.4
  python ml/terrain_seg/report.py --min-class-conf sand=0.55
  python ml/terrain_seg/report.py --set holdout --bias sand=-1.5
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_raw_navcam import render, sample_images  # noqa: E402
from sand_threshold import image_probs  # noqa: E402
from train import CLASSES, IGNORE  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW = Path.home() / "mars-data/raw/navcam_right_review"
CACHE = Path.home() / "mars-data/work/terrain_seg/navcam_right_probs"
MODEL = Path.home() / "mars-data/models/ai4mars_segformer_b2"
KNOWN_BAD_SOLS = {17, 18, 525, 1041, 1121, 1367, 1652}
SHORT = {"soil": "soil", "bedrock": "bedr", "sand": "sand", "big_rock": "rock"}


def parse_bias(items: list[str], default: float = 0.0) -> np.ndarray:
    b = np.full(len(CLASSES), default, np.float32)
    for it in items:
        name, _, val = it.partition("=")
        if name not in CLASSES:
            sys.exit(f"unknown class '{name}', use one of {CLASSES}")
        b[CLASSES.index(name)] = float(val)
    return b


def bias_tag(b: np.ndarray, min_conf: float, class_conf: np.ndarray, fallback: str) -> str:
    parts = [f"{SHORT[c]}{v:+.2g}" for c, v in zip(CLASSES, b, strict=True) if v != 0]
    parts += [f"{SHORT[c]}min{v:g}" for c, v in zip(CLASSES, class_conf, strict=True) if v > 0]
    tag = "_".join(parts) or "nobias"
    if class_conf.any() and fallback == "none":
        tag += "_drop"
    return tag + (f"_conf{min_conf:g}" if min_conf != 0.5 else "")


def load_picks(which: str) -> list[dict]:
    dev = json.loads((RAW / "picks.json").read_text())
    if which == "dev":
        return sorted(dev, key=lambda p: p["sol"])
    f = RAW / "picks_holdout.json"
    if not f.exists():
        exclude = KNOWN_BAD_SOLS | {p["sol"] for p in dev}
        picks = sample_images(30, 150, (-25.0, 0.0), exclude, offset=0.5)
        f.write_text(json.dumps(picks, indent=1))
        print(f"holdout set built: {len(picks)} views, sols {sorted(p['sol'] for p in picks)}")
    return sorted(json.loads(f.read_text()), key=lambda p: p["sol"])


def cached_probs(im: dict, model_box: dict, device) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    f = RAW / im["image_files"]["large"].rsplit("/", 1)[-1]
    if not f.exists():
        import requests

        r = requests.get(im["image_files"]["large"], timeout=120,
                         headers={"User-Agent": "mars-street-view-review/0.1 (research)"})
        r.raise_for_status()
        f.write_bytes(r.content)
    c = CACHE / f"{im['imageid']}.npz"
    rgb = np.asarray(Image.open(f).convert("RGB"))
    if c.exists():
        z = np.load(c)
        return rgb, z["prob"].astype(np.float32), z["valid"]
    if "model" not in model_box:
        from transformers import SegformerForSemanticSegmentation

        model = SegformerForSemanticSegmentation.from_pretrained(MODEL)
        model_box["model"] = model.to(device).eval()
    _, prob, valid = image_probs(model_box["model"], device, f, im)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(c, prob=prob.astype(np.float16), valid=valid)
    return rgb, prob, valid


def classify(prob: np.ndarray, valid: np.ndarray, bias: np.ndarray, min_conf: float,
             class_conf: np.ndarray, fallback: str = "next") -> np.ndarray:
    logp = np.log(np.clip(prob, 1e-8, 1)) + bias[:, None, None]
    dropped = np.zeros(prob.shape[1:], bool)
    for _ in range(len(CLASSES)):
        cls = logp.argmax(0)
        low = np.zeros_like(dropped)
        for c in np.nonzero(class_conf)[0]:
            low |= (cls == c) & (prob[c] < class_conf[c])
        if not low.any():
            break
        if fallback == "none":
            dropped |= low
            break
        rows, cols = np.nonzero(low)
        logp[cls[rows, cols], rows, cols] = -np.inf
    cls = logp.argmax(0).astype(np.uint8)
    cls[~valid | (prob.max(0) < min_conf) | dropped] = IGNORE
    return cls


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=["dev", "holdout"], default="dev")
    ap.add_argument("--bias", nargs="*", default=[], metavar="CLASS=VALUE",
                    help=f"log-probability bias per class; classes: {', '.join(CLASSES)}")
    ap.add_argument("--min-conf", type=float, default=0.5,
                    help="pixels whose top probability is below this stay uncoloured")
    ap.add_argument("--min-class-conf", nargs="*", default=[], metavar="CLASS=P",
                    help="minimum probability for a class to be kept, e.g. sand=0.55")
    ap.add_argument("--fallback", choices=["next", "none"], default="next",
                    help="pixels failing --min-class-conf: next likely class, or uncoloured")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    bias = parse_bias(a.bias)
    class_conf = parse_bias(a.min_class_conf)
    tag = bias_tag(bias, a.min_conf, class_conf, a.fallback)
    out = a.out or ROOT / "review/terrain_seg" / f"{a.set}_{tag}"
    out.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    picks, box, rows = load_picks(a.set), {}, []

    w = 9
    shown_bias = {c: round(float(v), 3) for c, v in zip(CLASSES, bias, strict=True)}
    shown_conf = {c: round(float(v), 3) for c, v in zip(CLASSES, class_conf, strict=True) if v > 0}
    print(f"set {a.set}, bias {shown_bias}, min_conf {a.min_conf}, "
          f"min class conf {shown_conf}, fallback {a.fallback}\n")
    print(f"{'frame':14s} | {'mean probability':^{4 * w}s} | {'share after bias':^{4 * w}s} | none")
    print(f"{'':14s} | " + "".join(f"{SHORT[c]:>{w}s}" for c in CLASSES) + " | "
          + "".join(f"{SHORT[c]:>{w}s}" for c in CLASSES) + " |")
    totals_p, totals_s = np.zeros(4), np.zeros(4)
    for i, im in enumerate(picks, 1):
        rgb, prob, valid = cached_probs(im, box, device)
        cls = classify(prob, valid, bias, a.min_conf, class_conf, a.fallback)
        mean_p = prob[:, valid].mean(1) if valid.any() else np.zeros(4)
        lab = cls != IGNORE
        counts = np.bincount(cls[lab], minlength=4)[:4]
        share = counts / max(counts.sum(), 1)
        none = 1 - lab.sum() / max(valid.sum(), 1)
        totals_p += mean_p
        totals_s += share
        name = f"{i:02d}_sol{im['sol']:04d}"
        panel, _ = render(rgb, cls, i, im, note=tag)
        panel.save(out / f"{name}.png")
        print(f"{name:14s} | " + "".join(f"{v:{w}.1%}" for v in mean_p) + " | "
              + "".join(f"{v:{w}.1%}" for v in share) + f" | {none:5.1%}", flush=True)
        rows.append({"id": i, "file": f"{name}.png", "sol": im["sol"], "imageid": im["imageid"],
                     **{f"p_{c}": round(float(v), 3) for c, v in zip(CLASSES, mean_p, strict=True)},
                     **{f"share_{c}": round(float(v), 3)
                        for c, v in zip(CLASSES, share, strict=True)},
                     "verdict (ok / partly / wrong)": "", "true_class": "", "comment": ""})
    n = max(len(picks), 1)
    print(f"{'mean':14s} | " + "".join(f"{v / n:{w}.1%}" for v in totals_p) + " | "
          + "".join(f"{v / n:{w}.1%}" for v in totals_s) + " |")
    with open(out / "review.csv", "w", newline="", encoding="utf-8-sig") as fo:
        wr = csv.DictWriter(fo, fieldnames=list(rows[0]), delimiter=";")
        wr.writeheader()
        wr.writerows(rows)
    print(f"\nreport -> {out}")


if __name__ == "__main__":
    main()
