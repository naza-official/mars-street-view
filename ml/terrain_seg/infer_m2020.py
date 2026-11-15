"""Run the AI4Mars SegFormer on a Perseverance stop panorama and summarise terrain classes.

Input  : $MSV_DATA/raw/l4_streetview/<stop>/mosaic/  (CYL RAS colour panorama + CYL RNG range)
         model dir from train.py
Output : $MSV_DATA/work/terrain_seg/<stop>/
           classes.png     class index per panorama pixel (255 = no prediction / no range)
           overlay.jpg     panorama with class colours (rover darkened, >30 m and sky uncoloured)
           summary.json    class shares by range band (0-5, 5-15, 15-30 m), pixel-weighted

Scale match: training frames are Curiosity Navcam at ~1.6 mrad/px after 1024 -> 512; the panorama is
resampled to the same angular scale before inference, then predictions are mapped back.

Usage: python ml/terrain_seg/infer_m2020.py --stop m2020_site1_drive52
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "layers"))
sys.path.insert(0, str(ROOT / "layers" / "l4_streetview"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pano as P  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from b_common.mars import ned_to_mars_fixed_matrix  # noqa: E402
from b_common.pds4 import cylindrical, read_array  # noqa: E402
from train import CLASSES, IGNORE, to_tensor  # noqa: E402
from transformers import SegformerForSemanticSegmentation  # noqa: E402

COLOURS = np.array([[215, 160, 90], [60, 120, 230], [250, 220, 40], [230, 40, 40]], np.uint8)
TRAIN_MRAD_PER_PX = 1.6
BANDS_M = [(0, 5), (5, 15), (15, 30)]


def find(mosaic: Path, kind: str) -> Path:
    for variant in ("UNCORCL", "UNCORCT", "UNCORCG"):
        hits = sorted(mosaic.glob(f"N_LRGB_*_{kind}_*_CYL_S_{variant}*.xml"))
        if hits:
            return hits[-1]
    raise FileNotFoundError(f"no CYL {kind} mosaic in {mosaic}")


def gray_panorama(lbl: Path) -> np.ndarray:
    rgba = P.tone_map(read_array(lbl))
    g = np.asarray(Image.fromarray(rgba[..., :3]).convert("L"))
    g = g.copy()
    g[rgba[..., 3] == 0] = 0
    return g


@torch.no_grad()
def predict(model, gray: np.ndarray, device, tile: int = 1024, overlap: int = 128) -> np.ndarray:
    h, w = gray.shape
    acc = torch.zeros(len(CLASSES), h, w)
    cnt = torch.zeros(1, h, w)
    step = tile - overlap
    for y in range(0, max(h - overlap, 1), step):
        for x in range(0, max(w - overlap, 1), step):
            y1, x1 = min(y + tile, h), min(x + tile, w)
            y0, x0 = max(0, y1 - tile), max(0, x1 - tile)
            t = to_tensor(gray[y0:y1, x0:x1])[None].to(device)
            with torch.autocast("cuda", dtype=torch.float16, enabled=device.type == "cuda"):
                lg = model(pixel_values=t).logits
            lg = F.interpolate(lg.float(), size=(y1 - y0, x1 - x0), mode="bilinear",
                               align_corners=False)
            acc[:, y0:y1, x0:x1] += lg.softmax(1)[0].cpu()
            cnt[:, y0:y1, x0:x1] += 1
    return (acc / cnt.clamp(min=1)).numpy()


def range_on_ras_grid(rng_lbl: Path, ras_lbl: Path, shape: tuple[int, int]) -> np.ndarray:
    rng = read_array(rng_lbl)[0]
    c_rng, c_ras = cylindrical(rng_lbl), cylindrical(ras_lbl)
    out = np.full(shape, np.nan, np.float32)
    shift = int(round(c_rng.zero_elevation_line - c_ras.zero_elevation_line))
    for r in range(shape[0]):
        rr = r + shift
        if 0 <= rr < rng.shape[0]:
            out[r] = rng[rr, : shape[1]]
    return out


def rover_offset_ned(data: Path, stop: str) -> np.ndarray:
    """Rover nav-frame origin relative to the SITE origin, in the SITE (north-east-down) frame."""
    site, drive = (int(v) for v in stop.split("_site")[1].split("_drive"))
    l3 = sorted((data / "rover/l3_poses/jezero").glob("v*/poses.parquet"))[-1]
    t = pq.read_table(l3, columns=["site", "drive", "lon", "lat", "x_m", "y_m", "z_m"]).to_pylist()
    row = next(r for r in t if r["site"] == site and r["drive"] == drive)
    org = next(r for r in t if r["site"] == site and r["drive"] == 0)
    d = np.array([row["x_m"] - org["x_m"], row["y_m"] - org["y_m"], row["z_m"] - org["z_m"]])
    return ned_to_mars_fixed_matrix(org["lon"], org["lat"]).T @ d


def xyz_on_ras_grid(xyz_lbl: Path, ras_lbl: Path, shape: tuple[int, int]) -> np.ndarray:
    xyz = read_array(xyz_lbl)
    z_xyz = cylindrical(xyz_lbl).zero_elevation_line
    z_ras = cylindrical(ras_lbl).zero_elevation_line
    shift = int(round(z_xyz - z_ras))
    out = np.full((3, *shape), np.nan, np.float32)
    for r in range(shape[0]):
        if 0 <= r + shift < xyz.shape[1]:
            out[:, r] = xyz[:, r + shift, : shape[1]]
    return out


def rover_mask(xyz: np.ndarray, rover_ned: np.ndarray, body_r: float = 2.3,
               near_r: float = 4.0, above: float = 0.3) -> np.ndarray:
    """Rover pixels: within body_r horizontally, or within near_r and well above the ground."""
    dn, de = xyz[0] - rover_ned[0], xyz[1] - rover_ned[1]
    horiz = np.hypot(dn, de)
    ring = (horiz > 3.0) & (horiz < 10.0)
    ground_z = np.nanmedian(xyz[2][ring]) if ring.any() else rover_ned[2]
    height = ground_z - xyz[2]
    with np.errstate(invalid="ignore"):
        return (horiz < body_r) | ((horiz < near_r) & (height > above))


def summarise(cls: np.ndarray, rng: np.ndarray) -> dict:
    bands = {}
    for lo, hi in BANDS_M:
        m = (rng >= lo) & (rng < hi) & (cls != IGNORE)
        n = int(m.sum())
        counts = np.bincount(cls[m], minlength=len(CLASSES))[: len(CLASSES)]
        bands[f"{lo}-{hi}m"] = {"pixels": n, "share": dict(
            zip(CLASSES, (counts / max(n, 1)).round(4).tolist(), strict=True))}
    return bands


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stop", default="m2020_site1_drive52")
    ap.add_argument("--data", type=Path, default=Path.home() / "mars-data")
    ap.add_argument("--model", type=Path,
                    default=Path.home() / "mars-data/models/ai4mars_segformer_b2")
    ap.add_argument("--min-conf", type=float, default=0.5)
    a = ap.parse_args()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    mosaic = a.data / "raw/l4_streetview" / a.stop / "mosaic"
    out = a.data / "work/terrain_seg" / a.stop
    out.mkdir(parents=True, exist_ok=True)

    ras = find(mosaic, "RAS")
    cyl = cylindrical(ras)
    gray = gray_panorama(ras)
    pano_mrad = 1000 * np.radians(1 / cyl.pixel_scale_deg)
    s = pano_mrad / TRAIN_MRAD_PER_PX
    small = np.asarray(Image.fromarray(gray).resize(
        (int(gray.shape[1] * s), int(gray.shape[0] * s)), Image.BILINEAR))
    print(f"{ras.name}: {gray.shape} -> {small.shape} "
          f"({pano_mrad:.2f} -> {TRAIN_MRAD_PER_PX} mrad/px)")

    model = SegformerForSemanticSegmentation.from_pretrained(a.model).to(device).eval()
    prob = predict(model, small, device)
    prob = F.interpolate(torch.from_numpy(prob)[None], size=gray.shape, mode="bilinear",
                         align_corners=False)[0].numpy()
    cls = prob.argmax(0).astype(np.uint8)
    cls[(prob.max(0) < a.min_conf) | (gray == 0)] = IGNORE

    rng = range_on_ras_grid(find(mosaic, "RNG"), ras, gray.shape)
    xyz = xyz_on_ras_grid(find(mosaic, "XYZ"), ras, gray.shape)
    rover = rover_mask(xyz, rover_offset_ned(a.data, a.stop))
    keep = ~rover & np.isfinite(rng) & (rng <= BANDS_M[-1][1])
    cls[~keep] = IGNORE
    print(f"rover pixels masked: {int(rover.sum())}, kept with range <= 30 m: {int(keep.sum())}")
    Image.fromarray(cls).save(out / "classes.png")
    rgb = np.repeat(gray[..., None], 3, axis=2).astype(np.float32)
    lab = cls != IGNORE
    rgb[lab] = 0.5 * rgb[lab] + 0.5 * COLOURS[cls[lab]]
    rgb[rover] = 0.35 * rgb[rover]
    Image.fromarray(rgb.astype(np.uint8)).save(out / "overlay.jpg", quality=88)
    summary = {"stop": a.stop, "source": ras.stem, "model": str(a.model.name),
               "min_conf": a.min_conf, "range_bands": summarise(cls, rng),
               "pixels_with_range": int(np.isfinite(rng).sum())}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["range_bands"], indent=1))
    print("->", out)


if __name__ == "__main__":
    main()
