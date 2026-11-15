"""Terrain-class evidence grid: Navcam frames -> class probabilities -> ray cast -> map cells.

For every frame (raw-images JPEG with its CAHVORE model) near the demo area: run the AI4Mars model,
cast rays through a sparse pixel grid onto the PLACES DEM, and add each hit to a map cell:
    sum_p[c] += p_c * w,  w = 1 / max(range, 3 m),   weight += w,   count += 1
Probabilities are stored, not classes, so the decision rule (e.g. sand >= 0.55) can change later
without re-projecting.

Masked out: sky, rover body (hits within 2.5 m horizontally of the rover), hits beyond --max-range.

Output: $MSV_DATA/work/terrain_seg/evidence/<name>/
  (in evidence/<name>/)
  evidence.npz       sum_p (4,H,W), weight, count, grid origin (PLACES easting/northing), cell size
  evidence_rgb.png   class map (decision rule applied) for a quick look, north up
  frames.json        frames used

Usage: python ml/terrain_seg/evidence_grid.py --name seitah --sol-min 200 --sol-max 260
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from infer_m2020 import COLOURS  # noqa: E402
from raycast import Cahvore, Dem, cast, quat_xyzw_to_matrix  # noqa: E402
from report import classify  # noqa: E402
from train import CLASSES  # noqa: E402

DATA = Path.home() / "mars-data"
META_BY_NAME = {"landing": DATA / "raw/navcam_demo"}
CACHE = DATA / "work/terrain_seg/navcam_demo_probs"
EVIDENCE = DATA / "work/terrain_seg/evidence"
MODEL = DATA / "models/ai4mars_segformer_b2"
L3 = DATA / "rover/l3_poses/jezero/v1/poses.parquet"
UA = {"User-Agent": "mars-street-view-review/0.1 (research)"}


def meta_dir(name: str) -> Path:
    return META_BY_NAME.get(name, DATA / "raw/navcam_meta" / name)


def frames(meta: Path, sol_min: int, sol_max: int) -> list[dict]:
    ims, seen = [], set()
    for f in glob.glob(str(meta / "NAVCAM_*_p*.json")):
        try:
            batch = json.loads(Path(f).read_text())["images"]
        except (ValueError, KeyError):
            continue
        for im in batch:
            e = im.get("extended", {})
            comp = im.get("camera", {}).get("camera_model_component_list", "")
            if (
                im["imageid"] in seen
                or not sol_min <= im["sol"] <= sol_max
                or "UNK" in comp
                or not comp
                or e.get("scaleFactor") not in ("2", "4")
            ):
                continue
            seen.add(im["imageid"])
            ims.append(im)
    return sorted(ims, key=lambda i: i["imageid"])


def prefetch(ims: list[dict], meta: Path, workers: int = 8) -> int:
    """Download the JPEGs of frames that have no cached probabilities yet, in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    img = meta / "img"
    img.mkdir(parents=True, exist_ok=True)
    todo = [
        im["image_files"]["large"]
        for im in ims
        if not (CACHE / f"{im['imageid']}.npz").exists()
        and not (img / im["image_files"]["large"].rsplit("/", 1)[-1]).exists()
    ]
    s = requests.Session()
    s.headers.update(UA)

    def get(url: str) -> int:
        for _ in range(3):
            try:
                r = s.get(url, timeout=120)
                r.raise_for_status()
                (img / url.rsplit("/", 1)[-1]).write_bytes(r.content)
                return 1
            except requests.RequestException:
                continue
        return 0

    with ThreadPoolExecutor(workers) as ex:
        return sum(ex.map(get, todo))


def sample_grid(h: int, w: int, stride: int) -> tuple[np.ndarray, np.ndarray]:
    return np.mgrid[stride // 2 : h : stride, stride // 2 : w : stride]


def probs(im: dict, box: dict, device, meta: Path, stride: int) -> tuple[np.ndarray, ...]:
    """Class probabilities and sky-free mask at the ray-grid pixels only (cache ~36x smaller).

    Returns prob_s (4, gh, gw), valid_s (gh, gw), full JPEG shape (h, w). Old full-frame caches
    are subsampled and rewritten in the compact form on first read.
    """
    from sand_threshold import image_probs

    c = CACHE / f"{im['imageid']}.npz"
    if c.exists():
        z = np.load(c)
        if "prob_s" in z and int(z["stride"]) == stride:
            return z["prob_s"].astype(np.float32), z["valid_s"], tuple(z["shape"])
        if "prob" in z:
            prob, valid = z["prob"], z["valid"]
            yj, xj = sample_grid(*valid.shape, stride)
            out = (prob[:, yj, xj].astype(np.float16), valid[yj, xj], valid.shape)
            np.savez_compressed(c, prob_s=out[0], valid_s=out[1], shape=out[2], stride=stride)
            return out[0].astype(np.float32), out[1], tuple(out[2])
    f = meta / "img" / im["image_files"]["large"].rsplit("/", 1)[-1]
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(im["image_files"]["large"], timeout=120, headers=UA)
        r.raise_for_status()
        f.write_bytes(r.content)
    if "model" not in box:
        from transformers import SegformerForSemanticSegmentation

        box["model"] = SegformerForSemanticSegmentation.from_pretrained(MODEL).to(device).eval()
    _, prob, valid = image_probs(box["model"], device, f, im)
    yj, xj = sample_grid(*valid.shape, stride)
    prob_s, valid_s = prob[:, yj, xj].astype(np.float16), valid[yj, xj]
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(c, prob_s=prob_s, valid_s=valid_s, shape=valid.shape, stride=stride)
    return prob_s.astype(np.float32), valid_s, valid.shape


def project_frame(
    im: dict,
    prob_s: np.ndarray,
    valid_s: np.ndarray,
    shape: tuple[int, int],
    pose: pd.Series,
    dem: Dem,
    stride: int,
    max_range: float,
):
    h_j, w_j = shape
    dim_w, dim_h = (int(v) for v in im["extended"]["dimension"].strip("()").split(","))
    yj, xj = sample_grid(h_j, w_j, stride)
    keep = valid_s
    p = prob_s[:, keep]
    yj, xj = yj[keep], xj[keep]
    xd = (xj + 0.5) * dim_w / w_j - 0.5
    yd = (yj + 0.5) * dim_h / h_j - 0.5
    cam = Cahvore.from_raw(im["camera"]["camera_model_component_list"])
    cc, ray = cam.rays(xd, yd)
    rot = quat_xyzw_to_matrix(np.array(pose.attitude_quat_ned))
    rover = np.array([pose.places_easting_m, pose.places_northing_m, pose.elevation_above_areoid_m])
    pts, rng = cast(dem, cc @ rot.T, ray @ rot.T, rover, max_range=max_range)
    horiz = np.hypot(pts[:, 0] - rover[0], pts[:, 1] - rover[1])
    ok = np.isfinite(rng) & (horiz > 2.5)
    return pts[ok], rng[ok], p[:, ok]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--name", default="landing", help="area name (evidence/<name>/)")
    ap.add_argument("--sol-min", type=int, default=0)
    ap.add_argument("--sol-max", type=int, default=44)
    ap.add_argument("--cell", type=float, default=0.5)
    ap.add_argument("--stride", type=int, default=6, help="pixel step of the ray grid (JPEG px)")
    ap.add_argument("--max-range", type=float, default=40.0)
    ap.add_argument("--min-class-conf", nargs="*", default=["sand=0.55"])
    a = ap.parse_args()
    import torch
    from report import parse_bias

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    poses = pd.read_parquet(L3).set_index(["site", "drive"])
    dem = Dem()
    meta = meta_dir(a.name)
    out = EVIDENCE / a.name
    ims = [
        i
        for i in frames(meta, a.sol_min, a.sol_max)
        if (int(i["site"]), int(i["drive"])) in poses.index
    ]
    print(f"{a.name}: {len(ims)} frames (scale 2/4, sols {a.sol_min}-{a.sol_max}) with an L3 pose")

    print(f"prefetched {prefetch(ims, meta)} images")
    hits, used, box = [], [], {}
    for k, im in enumerate(ims, 1):
        prob, valid, shape = probs(im, box, device, meta, a.stride)
        pose = poses.loc[(int(im["site"]), int(im["drive"]))]
        pts, rng, p = project_frame(im, prob, valid, shape, pose, dem, a.stride, a.max_range)
        if len(pts):
            hits.append((pts[:, 0], pts[:, 1], rng, p))
            used.append(
                {
                    "imageid": im["imageid"],
                    "sol": im["sol"],
                    "site": int(im["site"]),
                    "drive": int(im["drive"]),
                    "hits": int(len(pts)),
                }
            )
        if k % 25 == 0:
            print(
                f"  {k}/{len(ims)} frames, {sum(len(h[0]) for h in hits):,} ground hits", flush=True
            )

    e = np.concatenate([h[0] for h in hits])
    n = np.concatenate([h[1] for h in hits])
    r = np.concatenate([h[2] for h in hits])
    p = np.concatenate([h[3] for h in hits], axis=1)
    e0, n1 = np.floor(e.min()) - 5, np.ceil(n.max()) + 5
    W = int(np.ceil((e.max() + 5 - e0) / a.cell))
    H = int(np.ceil((n1 - (n.min() - 5)) / a.cell))
    col = ((e - e0) / a.cell).astype(int)
    row = ((n1 - n) / a.cell).astype(int)
    w = 1.0 / np.maximum(r, 3.0)
    flat = row * W + col
    sum_p = np.stack([np.bincount(flat, p[c] * w, H * W) for c in range(4)]).reshape(4, H, W)
    weight = np.bincount(flat, w, H * W).reshape(H, W)
    count = np.bincount(flat, minlength=H * W).reshape(H, W)
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out / "evidence.npz",
        sum_p=sum_p.astype(np.float32),
        weight=weight.astype(np.float32),
        count=count.astype(np.int32),
        origin_e=e0,
        origin_n=n1,
        cell=a.cell,
        classes=np.array(CLASSES),
    )
    (out / "frames.json").write_text(json.dumps(used, indent=1))

    mean_p = sum_p / np.maximum(weight, 1e-9)
    has = count > 0
    cls = classify(mean_p, has, np.zeros(4, np.float32), 0.0, parse_bias(a.min_class_conf))
    rgb = np.full((H, W, 3), 30, np.uint8)
    rgb[has] = COLOURS[np.minimum(cls[has], 3)]
    Image.fromarray(rgb).save(out / "evidence_rgb.png")
    shares = np.bincount(cls[has], minlength=4)[:4] / max(has.sum(), 1)
    print(
        f"grid {W}x{H} cells of {a.cell} m ({W * a.cell:.0f} x {H * a.cell:.0f} m), "
        f"{has.sum():,} observed cells, {len(used)} frames used"
    )
    print(
        "observed-cell class shares:", {c: f"{v:.1%}" for c, v in zip(CLASSES, shares, strict=True)}
    )
    print("->", out)


if __name__ == "__main__":
    main()
