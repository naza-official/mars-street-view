"""Demo: SAM regions on an orbital ortho window + rover evidence + kNN on region embeddings.

  1. SAM (ViT-B, automatic masks) on overlapping tiles of the 0.25 m ortho window -> region raster
     (small masks painted over large ones; masks > 400 m2 are tile-scale background and dropped).
     SAM only picks out objects (plates, boulders), not texture units, so every pixel it leaves
     is split into SLIC superpixels (~40 m2).
  2. Region features: patch embeddings averaged inside the region (PCA 16) - MOMO ViT-B/16
     (Mars orbital, default) or DINOv2-small (--embed dino) - plus physical features (brightness,
     colour ratios, texture, DEM slope and roughness).
  3. Labels: the evidence grid (evidence_grid.py) summed inside each region; a region is labelled
     when it has enough evidence weight and observed area. Label = class distribution.
  4. kNN (k nearest labelled regions, inverse-distance weights) predicts a distribution for every
     region; regions farther than the labelled-set distance threshold are "unknown".
  5. Spatial block cross-validation on the labelled regions (blocks of the evidence area).
The decision rule (sand >= 0.55, else next class) is applied to distributions at the end.

Input : $MSV_DATA/raw/places_map_window/<area>.npz,
        $MSV_DATA/work/terrain_seg/evidence/<area>/evidence.npz, $MSV_DATA/work/places_dem.npy
Output: $MSV_DATA/work/terrain_seg/regions/, <repo>/review/terrain_seg/region_knn_demo_<embed>.png

Usage: python ml/terrain_seg/region_knn.py
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
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from infer_m2020 import COLOURS  # noqa: E402
from raycast import DEM_E0, DEM_N0  # noqa: E402
from report import classify, parse_bias  # noqa: E402
from review_crops import font  # noqa: E402
from train import CLASSES  # noqa: E402

DATA = Path.home() / "mars-data"
ROOT = Path(__file__).resolve().parents[2]
OUT = DATA / "work/terrain_seg/regions"
SAM_CKPT = DATA / "models/sam/sam_vit_b_01ec64.pth"


def load_window(area: str = "landing") -> tuple[np.ndarray, float, float, float]:
    w = np.load(DATA / "raw/places_map_window" / f"{area}.npz")
    return (
        np.dstack([w["b0"], w["b1"], w["b2"]]),
        float(w["origin_e"]),
        float(w["origin_n"]),
        float(w["res"]),
    )


def sam_regions(
    rgb: np.ndarray,
    device,
    tile: int = 800,
    overlap: int = 160,
    min_area: int = 300,
    max_area: int = 6400,
    slic_px: int = 640,
) -> np.ndarray:
    from segment_anything import SamAutomaticMaskGenerator, sam_model_registry

    sam = sam_model_registry["vit_b"](checkpoint=str(SAM_CKPT)).to(device)
    gen = SamAutomaticMaskGenerator(
        sam,
        points_per_side=40,
        pred_iou_thresh=0.8,
        stability_score_thresh=0.85,
        min_mask_region_area=min_area,
        crop_n_layers=0,
    )
    h, w = rgb.shape[:2]
    masks = []
    step = tile - overlap
    for y in range(0, max(h - overlap, 1), step):
        for x in range(0, max(w - overlap, 1), step):
            y1, x1 = min(y + tile, h), min(x + tile, w)
            y0, x0 = max(0, y1 - tile), max(0, x1 - tile)
            for m in gen.generate(rgb[y0:y1, x0:x1]):
                if min_area <= m["area"] <= max_area:
                    full = np.zeros((h, w), bool)
                    full[y0:y1, x0:x1] = m["segmentation"]
                    masks.append((m["area"], full))
            print(f"  SAM tile ({y0},{x0}): {len(masks)} masks so far", flush=True)
    lab = np.zeros((h, w), np.int32)
    for k, (_, m) in enumerate(sorted(masks, key=lambda t: -t[0]), 1):
        lab[m] = k
    from skimage.segmentation import slic

    sp = slic(rgb, n_segments=max(1, (h * w) // slic_px), compactness=10, start_label=1)
    left = lab == 0
    lab[left] = lab.max() + sp[left]
    _, lab = np.unique(lab, return_inverse=True)
    return lab.reshape(h, w).astype(np.int32)


@torch.no_grad()
def dino_tokens(rgb: np.ndarray, device, crop: int = 448) -> tuple[np.ndarray, int]:
    from transformers import AutoModel

    model = AutoModel.from_pretrained("facebook/dinov2-small").to(device).eval()
    mean = np.array([0.485, 0.456, 0.406], np.float32)
    std = np.array([0.229, 0.224, 0.225], np.float32)
    h, w = rgb.shape[:2]
    p = 14
    gh, gw = h // p, w // p
    feats = np.zeros((gh, gw, model.config.hidden_size), np.float32)
    for y in range(0, gh * p, crop):
        for x in range(0, gw * p, crop):
            tile = rgb[y : y + crop, x : x + crop].astype(np.float32) / 255.0
            th, tw = (tile.shape[0] // p) * p, (tile.shape[1] // p) * p
            t = torch.from_numpy(((tile[:th, :tw] - mean) / std).transpose(2, 0, 1))[None].to(
                device
            )
            tok = model(pixel_values=t).last_hidden_state[0, 1:].cpu().numpy()
            feats[y // p : y // p + th // p, x // p : x // p + tw // p] = tok.reshape(
                th // p, tw // p, -1
            )
    return feats, p


MOMO_CKPT = DATA / "models/momo/momo_vitb16.pth"
_MODELS: dict = {}


@torch.no_grad()
def momo_tokens(
    rgb: np.ndarray, device, crop: int = 224, stride: int = 112
) -> tuple[np.ndarray, int]:
    """MOMO (Kerner Lab, CVPR 2026) ViT-B/16 MAE encoder, Mars-orbital pre-training.

    MAE patch tokens carry a strong position-in-crop signal; with independent crops this makes
    regions at the same in-crop position look alike (a grid artefact). So: native 224 px crops with
    50 % overlap, the mean token of each in-crop position is subtracted, overlaps are averaged.
    The checkpoint is a pickle; it is read with weights_only=True and only argparse.Namespace
    (stored training arguments) allowlisted, so no code from the file is executed.
    """
    import argparse as _argparse

    import timm

    if "momo" not in _MODELS:
        torch.serialization.add_safe_globals([_argparse.Namespace])
        sd = torch.load(MOMO_CKPT, map_location="cpu", weights_only=True)["model"]
        enc = {k: v for k, v in sd.items() if not k.startswith(("decoder", "mask_token"))}
        net = timm.create_model(
            "vit_base_patch16_224", pretrained=False, num_classes=0, dynamic_img_size=True
        )
        net.load_state_dict(enc, strict=True)
        _MODELS["momo"] = net.to(device).eval()
    model = _MODELS["momo"]
    mean = np.array([0.485, 0.456, 0.406], np.float32)
    std = np.array([0.229, 0.224, 0.225], np.float32)
    h, w = rgb.shape[:2]
    p = 16
    g = crop // p
    ys = list(range(0, h - crop + 1, stride)) + ([h - crop] if (h - crop) % stride else [])
    xs = list(range(0, w - crop + 1, stride)) + ([w - crop] if (w - crop) % stride else [])
    origins = [(y, x) for y in ys for x in xs]
    toks = np.zeros((len(origins), g, g, 768), np.float32)
    batch = 32
    for b in range(0, len(origins), batch):
        chunk = origins[b : b + batch]
        arr = np.stack(
            [
                ((rgb[y : y + crop, x : x + crop].astype(np.float32) / 255.0 - mean) / std)
                for y, x in chunk
            ]
        ).transpose(0, 3, 1, 2)
        out = model.forward_features(torch.from_numpy(arr).to(device))[:, 1:]
        toks[b : b + len(chunk)] = out.cpu().numpy().reshape(len(chunk), g, g, -1)
    toks -= toks.mean(0, keepdims=True)
    gh, gw = h // p, w // p
    feats = np.zeros((gh, gw, 768), np.float32)
    cnt = np.zeros((gh, gw, 1), np.float32)
    for (y, x), t in zip(origins, toks, strict=True):
        ty, tx = y // p, x // p
        hh, ww = min(g, gh - ty), min(g, gw - tx)
        feats[ty : ty + hh, tx : tx + ww] += t[:hh, :ww]
        cnt[ty : ty + hh, tx : tx + ww] += 1
    return feats / np.maximum(cnt, 1), p


def region_features(
    rgb: np.ndarray, lab: np.ndarray, tokens: np.ndarray, p: int, dem: np.ndarray
) -> np.ndarray:
    n = lab.max() + 1
    gh, gw = tokens.shape[:2]
    tok_lab = lab[p // 2 :: p, p // 2 :: p][:gh, :gw]
    flat = tokens.reshape(-1, tokens.shape[2])
    cnt = np.bincount(tok_lab.ravel(), minlength=n).astype(float)
    emb = np.stack([np.bincount(tok_lab.ravel(), flat[:, k], n) for k in range(flat.shape[1])], 1)
    centres = ndimage.center_of_mass(np.ones_like(lab), lab, range(n))
    for i in np.nonzero(cnt == 0)[0]:
        cy, cx = centres[i]
        emb[i] = tokens[min(int(cy) // p, gh - 1), min(int(cx) // p, gw - 1)]
        cnt[i] = 1
    emb /= cnt[:, None]
    g = rgb.astype(np.float32).mean(2)
    tex = np.sqrt(
        np.maximum(ndimage.uniform_filter(g * g, 7) - ndimage.uniform_filter(g, 7) ** 2, 0)
    )
    gy, gx = np.gradient(dem)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    rough = np.abs(dem - ndimage.uniform_filter(dem, 5))
    idx = range(n)
    phys = np.stack(
        [
            ndimage.mean(g, lab, idx),
            ndimage.standard_deviation(g, lab, idx),
            ndimage.mean(rgb[..., 0] / (rgb[..., 1] + 1.0), lab, idx),
            ndimage.mean(rgb[..., 2] / (rgb[..., 1] + 1.0), lab, idx),
            ndimage.mean(tex, lab, idx),
            ndimage.mean(slope, lab, idx),
            ndimage.mean(rough, lab, idx),
        ],
        1,
    )
    e = PCA(16, random_state=0).fit_transform(StandardScaler().fit_transform(emb))
    return np.hstack(
        [StandardScaler().fit_transform(e), StandardScaler().fit_transform(np.nan_to_num(phys))]
    )


def merge_similar(lab: np.ndarray, feat: np.ndarray, pct: float, max_area_px: int) -> np.ndarray:
    """Union adjacent regions whose feature distance is below the pct-th percentile of all
    adjacent-pair distances (closest pairs first), capped at max_area_px per merged region."""
    a = np.concatenate([lab[:, :-1].ravel(), lab[:-1, :].ravel()])
    b = np.concatenate([lab[:, 1:].ravel(), lab[1:, :].ravel()])
    m = a != b
    pairs = np.unique(np.sort(np.stack([a[m], b[m]], 1), axis=1), axis=0)
    d = np.linalg.norm(feat[pairs[:, 0]] - feat[pairs[:, 1]], axis=1)
    thr = np.percentile(d, pct)
    parent = np.arange(lab.max() + 1)
    size = np.bincount(lab.ravel()).astype(np.int64)

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for k in np.argsort(d):
        if d[k] > thr:
            break
        ra, rb = root(pairs[k, 0]), root(pairs[k, 1])
        if ra != rb and size[ra] + size[rb] <= max_area_px:
            parent[rb] = ra
            size[ra] += size[rb]
    roots = np.array([root(x) for x in range(len(parent))])
    _, new = np.unique(roots, return_inverse=True)
    return new[lab].astype(np.int32)


def region_labels(
    lab: np.ndarray, ev: dict, e0: float, n0: float, res: float, min_weight: float, min_cover: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cell = float(ev["cell"])
    h, w = lab.shape
    ys, xs = np.mgrid[0:h, 0:w]
    er = ((e0 + (xs + 0.5) * res - float(ev["origin_e"])) / cell).astype(int)
    nr = ((float(ev["origin_n"]) - (n0 - (ys + 0.5) * res)) / cell).astype(int)
    H, W = ev["count"].shape
    inside = (er >= 0) & (er < W) & (nr >= 0) & (nr < H)
    er, nr = np.clip(er, 0, W - 1), np.clip(nr, 0, H - 1)
    wgt = np.where(inside, ev["weight"][nr, er], 0) / 4.0
    obs = inside & (ev["count"][nr, er] > 0)
    n = lab.max() + 1
    sum_p = np.stack(
        [
            np.bincount(lab.ravel(), (np.where(inside, ev["sum_p"][c][nr, er], 0) / 4.0).ravel(), n)
            for c in range(4)
        ],
        1,
    )
    weight = np.bincount(lab.ravel(), wgt.ravel(), n)
    cover = np.bincount(lab.ravel(), obs.ravel(), n) / np.bincount(lab.ravel(), minlength=n)
    labelled = (weight >= min_weight) & (cover >= min_cover)
    dist = sum_p / np.maximum(weight[:, None], 1e-9)
    return labelled, dist, cover


def knn_predict(
    feat: np.ndarray, train_idx: np.ndarray, train_dist: np.ndarray, query: np.ndarray, k: int
) -> tuple[np.ndarray, np.ndarray]:
    d = np.linalg.norm(feat[query][:, None] - feat[train_idx][None], axis=2)
    nn = np.argsort(d, 1)[:, :k]
    dk = np.take_along_axis(d, nn, 1)
    w = 1.0 / (dk + 1e-3)
    pred = (train_dist[nn] * w[..., None]).sum(1) / w.sum(1, keepdims=True)
    return pred, dk.mean(1)


def decide(dist: np.ndarray, rule: np.ndarray) -> np.ndarray:
    c = classify(
        dist.T[:, :, None], np.ones((len(dist), 1), bool), np.zeros(4, np.float32), 0.0, rule
    )
    return c[:, 0]


def block_cv(feat, labelled, dist, centres_e, k, rule, blocks: int = 4) -> dict:
    idx = np.nonzero(labelled)[0]
    q = np.quantile(centres_e[idx], np.linspace(0, 1, blocks + 1))
    fold = np.clip(np.searchsorted(q, centres_e[idx], side="right") - 1, 0, blocks - 1)
    truth = decide(dist[idx], rule)
    pred_all = np.zeros(len(idx), int)
    prior = np.zeros(len(idx), int)
    for f in range(blocks):
        te, tr = idx[fold == f], idx[fold != f]
        if len(te) == 0 or len(tr) < k:
            continue
        p, _ = knn_predict(feat, tr, dist[tr], te, k)
        pred_all[fold == f] = decide(p, rule)
        prior[fold == f] = decide(dist[tr].mean(0, keepdims=True), rule)[0]
    acc = float((pred_all == truth).mean())
    base = float((prior == truth).mean())
    per = {
        c: float(((pred_all == i) & (truth == i)).sum() / max((truth == i).sum(), 1))
        for i, c in enumerate(CLASSES)
    }
    return {
        "labelled_regions": int(len(idx)),
        "knn_accuracy": acc,
        "majority_baseline": base,
        "recall_per_class": per,
    }


def colour_map(lab: np.ndarray, cls_region: np.ndarray, unknown: np.ndarray) -> np.ndarray:
    pal = np.vstack([COLOURS, [[90, 90, 90]]]).astype(np.uint8)
    rc = np.where(unknown, 4, cls_region)
    return pal[rc[lab]]


def boundaries(lab: np.ndarray) -> np.ndarray:
    return (np.diff(lab, axis=0, prepend=lab[:1]) != 0) | (
        np.diff(lab, axis=1, prepend=lab[:, :1]) != 0
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--k", type=int, default=7)
    ap.add_argument("--area", default="landing")
    ap.add_argument("--embed", choices=["dino", "momo"], default="momo")
    ap.add_argument("--min-weight", type=float, default=3.0)
    ap.add_argument("--min-cover", type=float, default=0.2)
    ap.add_argument(
        "--slic-px",
        type=int,
        default=1280,
        help="SLIC superpixel size in 0.25 m px (1280 ~ 80 m2)",
    )
    ap.add_argument(
        "--max-area", type=int, default=12800, help="largest SAM mask kept, px (12800 ~ 800 m2)"
    )
    ap.add_argument(
        "--merge-pct",
        type=float,
        default=15.0,
        help="merge adjacent regions closer than this percentile of neighbour "
        "feature distances (0 = off)",
    )
    ap.add_argument("--merge-max-m2", type=float, default=2000.0)
    ap.add_argument(
        "--unknown-scale",
        type=float,
        default=1.0,
        help="x the leave-one-out distance threshold; >1 labels farther regions",
    )
    ap.add_argument("--min-class-conf", nargs="*", default=["sand=0.55"])
    a = ap.parse_args()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    OUT.mkdir(parents=True, exist_ok=True)
    rgb, e0, n0, res = load_window(a.area)
    rule = parse_bias(a.min_class_conf)

    lab_f = OUT / a.area / f"regions_slic{a.slic_px}_max{a.max_area}.npy"
    lab_f.parent.mkdir(parents=True, exist_ok=True)
    if lab_f.exists():
        lab = np.load(lab_f)
    else:
        lab = sam_regions(rgb, device, max_area=a.max_area, slic_px=a.slic_px)
        np.save(lab_f, lab)
    n = lab.max() + 1
    print(f"{n} regions; median area {np.median(np.bincount(lab.ravel())) * res * res:.0f} m2")

    dem_full = np.load(DATA / "work/places_dem.npy", mmap_mode="r")
    c0, r0 = int(e0 - DEM_E0), int(DEM_N0 - n0)
    side = int(round(rgb.shape[0] * res))
    dem = np.kron(np.asarray(dem_full[r0 : r0 + side, c0 : c0 + side], np.float32), np.ones((4, 4)))
    dem = dem[: rgb.shape[0], : rgb.shape[1]]
    tokens, p = (momo_tokens if a.embed == "momo" else dino_tokens)(rgb, device)
    feat = region_features(rgb, lab, tokens, p, dem)
    if a.merge_pct > 0:
        before = lab.max() + 1
        lab = merge_similar(lab, feat, a.merge_pct, int(a.merge_max_m2 / (res * res)))
        feat = region_features(rgb, lab, tokens, p, dem)
        n = lab.max() + 1
        print(
            f"merged similar neighbours: {before} -> {n} regions; median area "
            f"{np.median(np.bincount(lab.ravel())) * res * res:.0f} m2"
        )

    ev = dict(np.load(DATA / "work/terrain_seg/evidence" / a.area / "evidence.npz"))
    labelled, dist, cover = region_labels(lab, ev, e0, n0, res, a.min_weight, a.min_cover)
    print(f"labelled regions: {labelled.sum()} of {n}")
    tr = np.nonzero(labelled)[0]
    pred, dmean = knn_predict(feat, tr, dist[tr], np.arange(n), a.k)
    loo = np.array(
        [np.sort(np.linalg.norm(feat[tr] - feat[i], axis=1))[1 : a.k + 1].mean() for i in tr]
    )
    d_star = float(np.percentile(loo, 95)) * a.unknown_scale
    final = np.where(labelled[:, None], dist, pred)
    unknown = ~labelled & (dmean > d_star)
    cls_r = decide(final, rule)

    centres = ndimage.center_of_mass(np.ones_like(lab), lab, range(n))
    ce = np.array([c[1] for c in centres])
    cv = block_cv(feat, labelled, dist, ce, a.k, rule)
    area = np.bincount(lab.ravel()) * res * res
    shares = {
        c: float(area[(cls_r == i) & ~unknown].sum() / area.sum()) for i, c in enumerate(CLASSES)
    }
    shares["unknown"] = float(area[unknown].sum() / area.sum())
    summary = {
        "embed": a.embed,
        "slic_px": a.slic_px,
        "max_area": a.max_area,
        "merge_pct": a.merge_pct,
        "unknown_scale": a.unknown_scale,
        "regions": int(n),
        "labelled": int(labelled.sum()),
        "k": a.k,
        "unknown_threshold": d_star,
        "area_share": shares,
        "block_cv": cv,
    }
    tag = f"{a.embed}_slic{a.slic_px}_m{a.merge_pct:g}_u{a.unknown_scale:g}"
    (OUT / f"summary_{tag}.json").write_text(json.dumps(summary, indent=2))
    np.savez_compressed(
        OUT / "regions_result.npz",
        lab=lab,
        dist=final,
        labelled=labelled,
        unknown=unknown,
        cls=cls_r,
        origin_e=e0,
        origin_n=n0,
        res=res,
    )

    edge = boundaries(lab)
    sam_vis = rgb.copy()
    sam_vis[edge] = [255, 255, 255]
    ev_vis = rgb.astype(float).copy()
    lab_vis = colour_map(lab, decide(dist, rule), ~labelled)
    m = labelled[lab]
    ev_vis[m] = 0.35 * ev_vis[m] + 0.65 * lab_vis[m]
    knn_vis = 0.35 * rgb + 0.65 * colour_map(lab, cls_r, unknown)
    tiles = [rgb, sam_vis, ev_vis.astype(np.uint8), knn_vis.astype(np.uint8)]
    titles = [
        "орто HiRISE (PLACES), 400x400 м",
        f"SAM+SLIC{' +злиття' if a.merge_pct else ''}: {n} регіонів",
        f"розмічені ровером: {labelled.sum()}",
        f"kNN {a.embed.upper()} (k={a.k}); сірий = невідомо",
    ]
    s = 700
    canvas = Image.new("RGB", (4 * s + 50, s + 80), (24, 24, 24))
    d = ImageDraw.Draw(canvas)
    for i, (t, ttl) in enumerate(zip(tiles, titles, strict=True)):
        canvas.paste(Image.fromarray(t).resize((s, s), Image.LANCZOS), (10 + i * (s + 10), 50))
        d.text((10 + i * (s + 10), 12), ttl, font=font(22), fill=(255, 255, 255))
    names = {"soil": "ґрунт", "bedrock": "скеля", "sand": "пісок", "big_rock": "камінь"}
    x = 10
    for i, c in enumerate(CLASSES):
        d.rectangle([x, s + 55, x + 18, s + 73], fill=tuple(int(v) for v in COLOURS[i]))
        d.text((x + 24, s + 53), names[c], font=font(18), fill=(230, 230, 230))
        x += 140
    out_png = ROOT / f"review/terrain_seg/region_knn_demo_{tag}.png"
    canvas.save(out_png)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    print("->", out_png)


if __name__ == "__main__":
    main()
