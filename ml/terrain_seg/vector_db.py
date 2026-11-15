"""Prototype: per-class vector database of MOMO region embeddings, classify one orbital frame.

Database: every region the rover evidence labels clearly (dominant class share >= --purity) becomes
a representative: L2-normalised MOMO ViT-B/16 embedding (mean of the region's patch tokens), class,
area, evidence weight. Stored as vector_db.npz (swap for FAISS / pgvector at scale).

Areas: several 400 x 400 m orbital windows (landing, seitah, delta), one shared database.
Query (per window): for each region and each class, the mean cosine
distance to that class's k nearest representatives; the region takes the class with the smallest
mean distance. Every class competes on equal terms, so rare classes are not outvoted by soil.
"unknown" when even the best class is farther than --unknown-pct of the database's own
leave-one-out distances. Margin = second-best minus best distance (confidence proxy).

Check: leave-one-area-out - each area's representatives are classified with a database built only
from the OTHER areas (terrain the model has never been given labels for).

Output: $MSV_DATA/work/terrain_seg/vector_db/ and <repo>/review/terrain_seg/vector_db_<area>.png

Usage: python ml/terrain_seg/vector_db.py --k 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from infer_m2020 import COLOURS  # noqa: E402
from raycast import DEM_E0, DEM_N0  # noqa: E402
from region_knn import (  # noqa: E402
    DATA,
    ROOT,
    boundaries,
    load_window,
    merge_similar,
    momo_tokens,
    region_features,
    region_labels,
    sam_regions,
)
from report import parse_bias  # noqa: E402
from review_crops import font  # noqa: E402
from train import CLASSES  # noqa: E402

OUT = DATA / "work/terrain_seg/vector_db"
REGIONS = DATA / "work/terrain_seg/regions"
UA = {"soil": "ґрунт", "bedrock": "скеля", "sand": "пісок", "big_rock": "камінь"}


def region_embeddings(lab: np.ndarray, tokens: np.ndarray, p: int) -> np.ndarray:
    n = lab.max() + 1
    gh, gw = tokens.shape[:2]
    tl = lab[p // 2 :: p, p // 2 :: p][:gh, :gw].ravel()
    flat = tokens.reshape(-1, tokens.shape[2])
    cnt = np.bincount(tl, minlength=n).astype(float)
    emb = np.stack([np.bincount(tl, flat[:, j], n) for j in range(flat.shape[1])], 1)
    centres = ndimage.center_of_mass(np.ones_like(lab), lab, range(n))
    for i in np.nonzero(cnt == 0)[0]:
        cy, cx = centres[i]
        emb[i] = tokens[min(int(cy) // p, gh - 1), min(int(cx) // p, gw - 1)]
        cnt[i] = 1
    emb /= cnt[:, None]
    emb -= emb.mean(0, keepdims=True)
    return emb / np.linalg.norm(emb, axis=1, keepdims=True)


def hard_label(dist: np.ndarray, rule: np.ndarray) -> np.ndarray:
    from region_knn import decide

    return decide(dist, rule)


def per_class_scores(
    q: np.ndarray,
    db_emb: np.ndarray,
    db_cls: np.ndarray,
    k: int,
    exclude_self: np.ndarray | None = None,
) -> np.ndarray:
    """Mean cosine distance from each query to the k nearest representatives of every class.

    Returns (n_query, n_classes); inf for classes with fewer than k representatives.
    """
    d = 1.0 - q @ db_emb.T
    if exclude_self is not None:
        d[np.arange(len(q)), exclude_self] = np.inf
    out = np.full((len(q), len(CLASSES)), np.inf)
    for c in range(len(CLASSES)):
        cols = np.nonzero(db_cls == c)[0]
        if len(cols) >= k + (exclude_self is not None):
            dc = np.sort(d[:, cols], axis=1)[:, :k]
            out[:, c] = dc.mean(1)
    return out


def classify(scores: np.ndarray, thr: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    order = np.argsort(scores, axis=1)
    best = scores[np.arange(len(scores)), order[:, 0]]
    second = scores[np.arange(len(scores)), order[:, 1]]
    return order[:, 0], best > thr, np.where(np.isfinite(second), second - best, np.nan)


def block_cv(db_emb, db_cls, db_e, k, blocks=4) -> dict:
    q = np.quantile(db_e, np.linspace(0, 1, blocks + 1))
    fold = np.clip(np.searchsorted(q, db_e, side="right") - 1, 0, blocks - 1)
    pred = np.full(len(db_cls), -1)
    for f in range(blocks):
        te, tr = fold == f, fold != f
        s = per_class_scores(db_emb[te], db_emb[tr], db_cls[tr], k)
        pred[te] = np.argmin(s, 1)
    rec = {
        c: float(((pred == i) & (db_cls == i)).sum() / max((db_cls == i).sum(), 1))
        for i, c in enumerate(CLASSES)
        if (db_cls == i).any()
    }
    return {
        "accuracy": float((pred == db_cls).mean()),
        "balanced_accuracy": float(np.mean(list(rec.values()))),
        "majority_baseline": float(np.bincount(db_cls).max() / len(db_cls)),
        "recall_per_class": rec,
    }


def render(rgb, lab, db_mask, db_cls_r, cls_r, unknown, margin, n_db, out_png):
    pal = np.vstack([COLOURS, [[90, 90, 90]]]).astype(np.uint8)
    edge = boundaries(lab)
    seg = rgb.copy()
    seg[edge] = [255, 255, 255]
    rep = rgb.astype(float).copy()
    m = db_mask[lab]
    rep[m] = 0.35 * rep[m] + 0.65 * pal[db_cls_r[lab]][m]
    res = 0.35 * rgb + 0.65 * pal[np.where(unknown, 4, cls_r)[lab]]
    mg = np.nan_to_num(margin, nan=0.0)
    mg = np.clip(mg / max(np.percentile(mg, 95), 1e-6), 0, 1)
    conf = (np.stack([mg, mg, mg], -1)[lab] * 255).astype(np.uint8)
    tiles = [seg, rep.astype(np.uint8), res.astype(np.uint8), conf]
    titles = [
        f"регіони: {lab.max() + 1}",
        f"векторна база: {n_db} представників",
        "клас = найближчий за k=3 у кожному класі",
        "впевненість (відрив 1-го від 2-го)",
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


def prepare_area(area: str, a, device, rule) -> dict:
    rgb, e0, n0, res = load_window(area)
    lab_f = REGIONS / area / f"regions_slic{a.slic_px}_max{a.max_area}.npy"
    if lab_f.exists():
        lab = np.load(lab_f)
    else:
        lab = sam_regions(rgb, device, max_area=a.max_area, slic_px=a.slic_px)
        lab_f.parent.mkdir(parents=True, exist_ok=True)
        np.save(lab_f, lab)
    tokens, p = momo_tokens(rgb, device)
    if a.merge_pct > 0:
        dem_full = np.load(DATA / "work/places_dem.npy", mmap_mode="r")
        c0, r0 = int(e0 - DEM_E0), int(DEM_N0 - n0)
        side = int(round(rgb.shape[0] * res))
        dem = np.kron(
            np.asarray(dem_full[r0 : r0 + side, c0 : c0 + side], np.float32), np.ones((4, 4))
        )[: rgb.shape[0], : rgb.shape[1]]
        lab = merge_similar(
            lab,
            region_features(rgb, lab, tokens, p, dem),
            a.merge_pct,
            int(a.merge_max_m2 / (res * res)),
        )
    n = lab.max() + 1
    emb = region_embeddings(lab, tokens, p)
    ev = dict(np.load(DATA / "work/terrain_seg/evidence" / area / "evidence.npz"))
    labelled, dist, _ = region_labels(lab, ev, e0, n0, res, a.min_weight, a.min_cover)
    hard = hard_label(dist, rule)
    purity = dist[np.arange(n), hard]
    is_rep = labelled & (purity >= a.purity)
    centres = ndimage.center_of_mass(np.ones_like(lab), lab, range(n))
    ce = np.array([c[1] for c in centres]) * res + e0
    return {
        "area": area,
        "rgb": rgb,
        "lab": lab,
        "emb": emb,
        "hard": hard,
        "purity": purity,
        "is_rep": is_rep,
        "reps": np.nonzero(is_rep)[0],
        "ce": ce,
        "area_m2": np.bincount(lab.ravel()) * res * res,
        "e0": e0,
        "n0": n0,
        "res": res,
    }


def counts_of(cls: np.ndarray) -> dict:
    return {c: int((cls == i).sum()) for i, c in enumerate(CLASSES)}


def leave_one_area_out(areas: list[dict], k: int) -> dict:
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
            "accuracy": float((pred == truth).mean()),
            "balanced_accuracy": float(np.mean(list(rec.values()))),
            "majority_baseline": float(np.bincount(truth).max() / len(truth)),
            "recall_per_class": rec,
            "db_from_other_areas": counts_of(db_c),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--areas", nargs="*", default=["landing", "seitah", "delta"])
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--purity", type=float, default=0.6)
    ap.add_argument("--unknown-pct", type=float, default=95.0)
    ap.add_argument("--min-weight", type=float, default=3.0)
    ap.add_argument("--min-cover", type=float, default=0.2)
    ap.add_argument("--slic-px", type=int, default=1280)
    ap.add_argument("--max-area", type=int, default=12800)
    ap.add_argument("--merge-pct", type=float, default=15.0)
    ap.add_argument("--merge-max-m2", type=float, default=2000.0)
    ap.add_argument("--min-class-conf", nargs="*", default=["sand=0.55"])
    a = ap.parse_args()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    OUT.mkdir(parents=True, exist_ok=True)
    rule = parse_bias(a.min_class_conf)
    areas = [prepare_area(name, a, device, rule) for name in a.areas]
    for ar in areas:
        print(
            f"{ar['area']}: {ar['lab'].max() + 1} regions, "
            f"{len(ar['reps'])} representatives {counts_of(ar['hard'][ar['reps']])}"
        )

    db_e = np.concatenate([ar["emb"][ar["reps"]] for ar in areas]).astype(np.float32)
    db_c = np.concatenate([ar["hard"][ar["reps"]] for ar in areas])
    db_area = np.concatenate([[ar["area"]] * len(ar["reps"]) for ar in areas])
    np.savez_compressed(
        OUT / "vector_db.npz",
        embedding=db_e,
        label=db_c,
        label_name=np.array(CLASSES)[db_c],
        area=db_area,
        purity=np.concatenate([ar["purity"][ar["reps"]] for ar in areas]),
        area_m2=np.concatenate([ar["area_m2"][ar["reps"]] for ar in areas]),
        easting=np.concatenate([ar["ce"][ar["reps"]] for ar in areas]),
        region_id=np.concatenate([ar["reps"] for ar in areas]),
        model="MOMO ViT-B/16 (momo.pth), position-debiased mean patch token",
    )
    loo = per_class_scores(db_e, db_e, db_c, a.k, exclude_self=np.arange(len(db_c)))
    own = loo[np.arange(len(db_c)), db_c]
    thr = float(np.percentile(own[np.isfinite(own)], a.unknown_pct))

    per_area = {}
    for ar in areas:
        scores = per_class_scores(ar["emb"], db_e, db_c, a.k)
        cls_r, unknown, margin = classify(scores, thr)
        cls_r[ar["reps"]], unknown[ar["reps"]] = ar["hard"][ar["reps"]], False
        tot = ar["area_m2"].sum()
        shares = {
            c: float(ar["area_m2"][(cls_r == i) & ~unknown].sum() / tot)
            for i, c in enumerate(CLASSES)
        }
        shares["unknown"] = float(ar["area_m2"][unknown].sum() / tot)
        per_area[ar["area"]] = shares
        out_png = ROOT / f"review/terrain_seg/vector_db_{ar['area']}.png"
        render(
            ar["rgb"],
            ar["lab"],
            ar["is_rep"],
            ar["hard"],
            cls_r,
            unknown,
            margin,
            len(db_c),
            out_png,
        )
        np.savez_compressed(
            OUT / f"result_{ar['area']}.npz",
            lab=ar["lab"],
            cls=cls_r,
            unknown=unknown,
            margin=margin,
            scores=scores,
            origin_e=ar["e0"],
            origin_n=ar["n0"],
            res=ar["res"],
        )

    summary = {
        "k": a.k,
        "purity": a.purity,
        "database": counts_of(db_c),
        "unknown_threshold_cosine": thr,
        "area_share": per_area,
        "leave_one_area_out": leave_one_area_out(areas, a.k),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
