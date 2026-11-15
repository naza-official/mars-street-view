"""Patch-level vector database: MOMO patch tokens (16 px = 4 x 4 m) as representatives.

Region-level representatives (vector_db.py) average narrow bedrock outcrops away inside ~90 m2
regions. Here each MOMO patch token is a candidate representative, labelled with the rover
evidence inside its 4 x 4 m footprint; rock classes get a lower purity threshold because they are
rarely pure even at 4 m. Classification is per patch (mean cosine distance to the k nearest
representatives of each class, smallest wins); each region then takes the majority class of its
patches. Check: leave-one-area-out on patch representatives.

Output: $MSV_DATA/work/terrain_seg/vector_db_patch/ and
        <repo>/review/terrain_seg/vector_db_patch_<area>.png

Usage: python ml/terrain_seg/vector_db_patch.py --areas landing seitah delta floor deltaflat
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from infer_m2020 import COLOURS  # noqa: E402
from region_knn import DATA, ROOT, boundaries, load_window, momo_tokens, region_labels  # noqa: E402
from report import parse_bias  # noqa: E402
from review_crops import font  # noqa: E402
from train import CLASSES  # noqa: E402
from vector_db import (  # noqa: E402
    UA,
    counts_of,
    hard_label,
    per_class_scores,
    prepare_area,
)

OUT = DATA / "work/terrain_seg/vector_db_patch"
ROCK = (1, 3)


def patch_area(area: str, a, device, rule) -> dict:
    rgb, e0, n0, res = load_window(area)
    tokens, p = momo_tokens(rgb, device)
    gh, gw = tokens.shape[:2]
    emb = tokens.reshape(-1, tokens.shape[2]).astype(np.float32)
    emb -= emb.mean(0, keepdims=True)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    tok_lab = np.repeat(np.repeat(np.arange(gh * gw).reshape(gh, gw), p, 0), p, 1)
    full = np.full(rgb.shape[:2], gh * gw, np.int64)
    full[: tok_lab.shape[0], : tok_lab.shape[1]] = tok_lab
    ev = dict(np.load(DATA / "work/terrain_seg/evidence" / area / "evidence.npz"))
    labelled, dist, _ = region_labels(full, ev, e0, n0, res, a.min_weight, a.min_cover)
    labelled, dist = labelled[: gh * gw], dist[: gh * gw]
    hard = hard_label(dist, rule)
    purity = dist[np.arange(gh * gw), hard]
    need = np.where(np.isin(hard, ROCK), a.rock_purity, a.purity)
    is_rep = labelled & (purity >= need)
    rng = np.random.default_rng(0)
    reps = []
    for c in range(len(CLASSES)):
        idx = np.nonzero(is_rep & (hard == c))[0]
        if len(idx) > a.cap:
            idx = rng.choice(idx, a.cap, replace=False)
        reps.append(idx)
    reps = np.sort(np.concatenate(reps))
    return {
        "area": area,
        "rgb": rgb,
        "emb": emb,
        "hard": hard,
        "reps": reps,
        "gh": gh,
        "gw": gw,
        "p": p,
        "res": res,
        "e0": e0,
        "n0": n0,
    }


def loao(areas: list[dict], k: int) -> dict:
    out = {}
    for t in areas:
        others = [o for o in areas if o is not t]
        db_e = np.concatenate([o["emb"][o["reps"]] for o in others])
        db_c = np.concatenate([o["hard"][o["reps"]] for o in others])
        q, truth = t["emb"][t["reps"]], t["hard"][t["reps"]]
        pred = np.argmin(per_class_scores(q, db_e, db_c, k), 1)
        rec = {
            c: float(((pred == i) & (truth == i)).sum() / (truth == i).sum())
            for i, c in enumerate(CLASSES)
            if (truth == i).any()
        }
        out[t["area"]] = {
            "n": int(len(truth)),
            "truth": counts_of(truth),
            "accuracy": float((pred == truth).mean()),
            "balanced_accuracy": float(np.mean(list(rec.values()))),
            "majority_baseline": float(np.bincount(truth).max() / len(truth)),
            "recall_per_class": rec,
        }
    return out


def region_majority(lab: np.ndarray, patch_cls: np.ndarray, gh: int, gw: int, p: int) -> np.ndarray:
    pix = np.full(lab.shape, 4, np.int64)
    grid = np.repeat(np.repeat(patch_cls.reshape(gh, gw), p, 0), p, 1)
    pix[: grid.shape[0], : grid.shape[1]] = grid
    n = lab.max() + 1
    votes = np.stack([np.bincount(lab.ravel(), (pix == c).ravel(), n) for c in range(5)], 1)
    known = votes[:, :4]
    return np.where(known.sum(1) > 0, known.argmax(1), 4)


def render(
    ar: dict, lab: np.ndarray, patch_cls: np.ndarray, reg_cls: np.ndarray, out_png: Path, n_db: int
) -> None:
    pal = np.vstack([COLOURS, [[90, 90, 90]]]).astype(np.uint8)
    rgb, gh, gw, p = ar["rgb"], ar["gh"], ar["gw"], ar["p"]
    rep_mask = np.zeros(gh * gw, bool)
    rep_mask[ar["reps"]] = True
    rep_cls = np.where(rep_mask, ar["hard"], 4)

    def up(v: np.ndarray) -> np.ndarray:
        g = np.full(rgb.shape[:2], 4, np.int64)
        u = np.repeat(np.repeat(v.reshape(gh, gw), p, 0), p, 1)
        g[: u.shape[0], : u.shape[1]] = u
        return g

    rc = up(rep_cls)
    rep_vis = rgb.astype(float).copy()
    m = rc < 4
    rep_vis[m] = 0.35 * rep_vis[m] + 0.65 * pal[rc][m]
    patch_vis = 0.35 * rgb + 0.65 * pal[up(patch_cls)]
    reg_vis = 0.35 * rgb + 0.65 * pal[reg_cls[lab]]
    reg_vis[boundaries(lab)] = 255
    tiles = [rgb, rep_vis.astype(np.uint8), patch_vis.astype(np.uint8), reg_vis.astype(np.uint8)]
    titles = [
        f"{ar['area']}: орто 400x400 м",
        f"представники-патчі (база {n_db})",
        "клас кожного патча 4x4 м (k=3 на клас)",
        "клас регіону = більшість патчів",
    ]
    s = 700
    canvas = Image.new("RGB", (4 * s + 50, s + 80), (24, 24, 24))
    d = ImageDraw.Draw(canvas)
    for i, (t, ttl) in enumerate(zip(tiles, titles, strict=True)):
        canvas.paste(Image.fromarray(t).resize((s, s), Image.LANCZOS), (10 + i * (s + 10), 50))
        d.text((10 + i * (s + 10), 12), ttl, font=font(22), fill=(255, 255, 255))
    x = 10
    for i, c in enumerate(CLASSES):
        d.rectangle([x, s + 55, x + 18, s + 73], fill=tuple(int(v) for v in COLOURS[i]))
        d.text((x + 24, s + 53), UA[c], font=font(18), fill=(230, 230, 230))
        x += 140
    d.rectangle([x, s + 55, x + 18, s + 73], fill=(90, 90, 90))
    d.text((x + 24, s + 53), "невідомо", font=font(18), fill=(230, 230, 230))
    canvas.save(out_png)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--areas", nargs="*", default=["landing", "seitah", "delta", "floor", "deltaflat"]
    )
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--purity", type=float, default=0.6)
    ap.add_argument("--rock-purity", type=float, default=0.45)
    ap.add_argument("--min-weight", type=float, default=0.5)
    ap.add_argument("--min-cover", type=float, default=0.3)
    ap.add_argument("--cap", type=int, default=1500, help="max representatives per class per area")
    ap.add_argument("--unknown-pct", type=float, default=95.0)
    ap.add_argument("--min-class-conf", nargs="*", default=["sand=0.55"])
    ap.add_argument("--slic-px", type=int, default=1280)
    ap.add_argument("--max-area", type=int, default=12800)
    ap.add_argument("--merge-pct", type=float, default=15.0)
    ap.add_argument("--merge-max-m2", type=float, default=2000.0)
    a = ap.parse_args()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    OUT.mkdir(parents=True, exist_ok=True)
    rule = parse_bias(a.min_class_conf)
    areas = [patch_area(n, a, device, rule) for n in a.areas]
    for ar in areas:
        print(
            f"{ar['area']}: {len(ar['reps'])} patch representatives "
            f"{counts_of(ar['hard'][ar['reps']])}"
        )
    db_e = np.concatenate([ar["emb"][ar["reps"]] for ar in areas])
    db_c = np.concatenate([ar["hard"][ar["reps"]] for ar in areas])
    np.savez_compressed(
        OUT / "vector_db_patch.npz",
        embedding=db_e.astype(np.float16),
        label=db_c,
        area=np.concatenate([[ar["area"]] * len(ar["reps"]) for ar in areas]),
        patch_id=np.concatenate([ar["reps"] for ar in areas]),
    )
    sub = np.random.default_rng(1).choice(len(db_c), min(len(db_c), 6000), replace=False)
    own = per_class_scores(db_e[sub], db_e, db_c, a.k, exclude_self=sub)[
        np.arange(len(sub)), db_c[sub]
    ]
    thr = float(np.percentile(own[np.isfinite(own)], a.unknown_pct))

    shares = {}
    for ar in areas:
        scores = per_class_scores(ar["emb"], db_e, db_c, a.k)
        patch_cls = np.argmin(scores, 1)
        patch_cls[scores.min(1) > thr] = 4
        patch_cls[ar["reps"]] = ar["hard"][ar["reps"]]
        lab = prepare_area(ar["area"], a, device, rule)["lab"]
        reg_cls = region_majority(lab, patch_cls, ar["gh"], ar["gw"], ar["p"])
        tot = np.bincount(patch_cls, minlength=5) / len(patch_cls)
        shares[ar["area"]] = {n: float(v) for n, v in zip([*CLASSES, "unknown"], tot, strict=True)}
        render(
            ar,
            lab,
            patch_cls,
            reg_cls,
            ROOT / f"review/terrain_seg/vector_db_patch_{ar['area']}.png",
            len(db_c),
        )
    summary = {
        "k": a.k,
        "database": counts_of(db_c),
        "unknown_threshold_cosine": thr,
        "patch_share": shares,
        "leave_one_area_out": loao(areas, a.k),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
