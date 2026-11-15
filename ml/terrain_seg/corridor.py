"""On-demand terrain classification along a route corridor, with a tile cache, plus a demo route.

Tiles: a fixed global grid of 256 x 256 m (1024 x 1024 px of the 0.25 m PLACES orbital map), each
classified once and cached under work/terrain_seg/tile_cache/<db version>/. The cache key includes
the vector database version (hash of vector_db_patch.npz) and the embedding model, so rebuilding the
database invalidates old tiles automatically. A tile is classified live only when it is missing.

Per tile: the ortho (with a 28 m context margin, fetched with HTTP multi-range requests from the
PLACES map, no full download) -> MOMO patch tokens (4 x 4 m) -> per-class k-nearest search in the
patch vector database -> class per patch, "unknown" beyond the database distance threshold.

Route: cost per 4 m cell = class cost x (1 + (slope / 10 deg)^2); slopes > --max-slope and cells
outside the corridor are impassable; least-cost path between the end points (8-connected).

Output: $MSV_DATA/work/terrain_seg/corridor/<name>.{npz,json,geojson} and
        <repo>/review/terrain_seg/corridor_<name>.png

Usage:
  python ml/terrain_seg/corridor.py --name landing_to_seitah \\
      --start 4354494 1093300 --end 4354011 1092645 --width 300
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import requests
import torch
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from infer_m2020 import COLOURS  # noqa: E402
from raycast import DEM_E0, DEM_N0  # noqa: E402
from region_knn import DATA, ROOT, momo_tokens  # noqa: E402
from review_crops import font  # noqa: E402
from train import CLASSES  # noqa: E402
from vector_db import per_class_scores  # noqa: E402

MAP_URL = (
    "https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_rover_places/"
    "data_maps/m20_orbital_map.img"
)
MAP_RES, MAP_LINES, MAP_SAMPLES, MAP_HEADER = 0.25, 21920, 59200, 118400
TILE, CTX, P = 1024, 112, 16
DB_FILE = DATA / "work/terrain_seg/vector_db_patch/vector_db_patch.npz"
DB_SUMMARY = DATA / "work/terrain_seg/vector_db_patch/summary.json"
CACHE_ROOT = DATA / "work/terrain_seg/tile_cache"
OUT = DATA / "work/terrain_seg/corridor"
MODEL_ID = "momo_vitb16_debiased"
COST = {"soil": 1.0, "bedrock": 1.5, "sand": 3.0, "big_rock": 8.0, "unknown": 2.5}
UNKNOWN = 4


def db_version() -> str:
    h = hashlib.sha1(DB_FILE.read_bytes()).hexdigest()[:10]
    return f"{MODEL_ID}_{h}"


def fetch_rows(session: requests.Session, band: int, rows: range, c0: int, c1: int) -> np.ndarray:
    """Columns c0:c1 of the given rows of one band, via multi-range HTTP requests."""
    out = np.zeros((len(rows), c1 - c0), np.uint8)
    base = MAP_HEADER + band * MAP_LINES * MAP_SAMPLES
    rows = list(rows)
    for i in range(0, len(rows), 200):
        chunk = rows[i : i + 200]
        spans = [(base + r * MAP_SAMPLES + c0, base + r * MAP_SAMPLES + c1 - 1) for r in chunk]
        hdr = "bytes=" + ",".join(f"{a}-{b}" for a, b in spans)
        for attempt in range(4):
            try:
                resp = session.get(MAP_URL, headers={"Range": hdr}, timeout=300)
                if resp.status_code != 206:
                    raise requests.RequestException(f"HTTP {resp.status_code}")
                parts = parse_multipart(resp)
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))
        for k, (a, _) in enumerate(spans):
            out[i + k] = np.frombuffer(parts[a], np.uint8)
    return out


def parse_multipart(resp: requests.Response) -> dict[int, bytes]:
    ctype = resp.headers.get("Content-Type", "")
    body = resp.content
    if "multipart/byteranges" not in ctype:
        start = int(re.search(r"bytes (\d+)-", resp.headers["Content-Range"]).group(1))
        return {start: body}
    boundary = ctype.split("boundary=")[1].strip().strip('"').encode()
    parts = {}
    for seg in body.split(b"--" + boundary):
        if b"Content-Range" not in seg:
            continue
        head, _, data = seg.partition(b"\r\n\r\n")
        m = re.search(rb"bytes (\d+)-(\d+)/", head)
        a, b = int(m.group(1)), int(m.group(2))
        parts[a] = data[: b - a + 1]
    return parts


def tile_ortho(session: requests.Session, tx: int, ty: int) -> np.ndarray:
    r0, c0 = ty * TILE - CTX, tx * TILE - CTX
    r1, c1 = r0 + TILE + 2 * CTX, c0 + TILE + 2 * CTX
    rr0, cc0 = max(r0, 0), max(c0, 0)
    rr1, cc1 = min(r1, MAP_LINES), min(c1, MAP_SAMPLES)
    rgb = np.zeros((r1 - r0, c1 - c0, 3), np.uint8)
    for b in range(3):
        rgb[rr0 - r0 : rr1 - r0, cc0 - c0 : cc1 - c0, b] = fetch_rows(
            session, b, range(rr0, rr1), cc0, cc1
        )
    return rgb


def classify_tile(tx: int, ty: int, ctx: dict) -> dict:
    """Cached per-patch classes for one tile; computed live (and cached) when missing."""
    f = ctx["cache"] / f"tile_{tx}_{ty}.npz"
    if f.exists():
        z = np.load(f)
        ctx["stats"]["cached"] += 1
        return {k: z[k] for k in z.files}
    rgb = tile_ortho(ctx["session"], tx, ty)
    tokens, p = momo_tokens(rgb, ctx["device"])
    g = TILE // P
    c = CTX // P
    tok = tokens[c : c + g, c : c + g].reshape(-1, tokens.shape[2]).astype(np.float32)
    tok -= tok.mean(0, keepdims=True)
    tok /= np.linalg.norm(tok, axis=1, keepdims=True)
    scores = per_class_scores(tok, ctx["db_e"], ctx["db_c"], ctx["k"])
    cls = np.argmin(scores, 1)
    cls[scores.min(1) > ctx["thr"]] = UNKNOWN
    thumb = np.asarray(Image.fromarray(rgb[CTX : CTX + TILE, CTX : CTX + TILE]).resize((256, 256)))
    out = {
        "cls": cls.reshape(g, g).astype(np.uint8),
        "score": scores.reshape(g, g, -1).astype(np.float16),
        "thumb": thumb,
    }
    np.savez_compressed(f, **out)
    ctx["stats"]["computed"] += 1
    return out


def corridor_tiles(start: np.ndarray, end: np.ndarray, width: float) -> list[tuple[int, int]]:
    tile_m = TILE * MAP_RES
    length = np.linalg.norm(end - start)
    n = max(2, int(length / (tile_m / 4)))
    tiles = set()
    for t in np.linspace(0, 1, n):
        e, nn = start + t * (end - start)
        for de in np.arange(-width / 2, width / 2 + 1, tile_m / 4):
            for dn in np.arange(-width / 2, width / 2 + 1, tile_m / 4):
                col, row = (e + de - DEM_E0) / MAP_RES, (DEM_N0 - (nn + dn)) / MAP_RES
                tiles.add((int(col // TILE), int(row // TILE)))
    return sorted(tiles)


def slope_deg_4m(e0: float, n0: float, h: int, w: int) -> np.ndarray:
    dem = np.load(DATA / "work/places_dem.npy", mmap_mode="r")
    c0, r0 = int(round(e0 - DEM_E0)), int(round(DEM_N0 - n0))
    z = np.asarray(dem[r0 : r0 + h * 4, c0 : c0 + w * 4], np.float32)
    z = z.reshape(h, 4, w, 4).mean((1, 3))
    gy, gx = np.gradient(z, 4.0)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def route(cost: np.ndarray, a: tuple[int, int], b: tuple[int, int]) -> tuple[np.ndarray, float]:
    from skimage.graph import route_through_array

    path, total = route_through_array(cost, a, b, fully_connected=True, geometric=True)
    return np.array(path), float(total)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--name", default="landing_to_seitah")
    ap.add_argument(
        "--start",
        nargs=2,
        type=float,
        default=[4354494.0, 1093300.0],
        metavar=("E", "N"),
        help="PLACES easting/northing, m",
    )
    ap.add_argument(
        "--end", nargs=2, type=float, default=[4354011.0, 1092645.0], metavar=("E", "N")
    )
    ap.add_argument("--width", type=float, default=300.0, help="corridor width, m")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--max-slope", type=float, default=25.0)
    a = ap.parse_args()
    t0 = time.time()
    db = np.load(DB_FILE)
    ver = db_version()
    cache = CACHE_ROOT / ver
    cache.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "mars-street-view/0.1 (research)"
    ctx = {
        "cache": cache,
        "session": session,
        "k": a.k,
        "device": torch.device("cuda" if torch.cuda.is_available() else "cpu"),
        "db_e": db["embedding"].astype(np.float32),
        "db_c": db["label"],
        "thr": json.loads(DB_SUMMARY.read_text())["unknown_threshold_cosine"],
        "stats": {"computed": 0, "cached": 0},
    }
    start, end = np.array(a.start), np.array(a.end)
    tiles = corridor_tiles(start, end, a.width)
    txs, tys = [t[0] for t in tiles], [t[1] for t in tiles]
    tx0, ty0 = min(txs), min(tys)
    g = TILE // P
    W, H = (max(txs) - tx0 + 1) * g, (max(tys) - ty0 + 1) * g
    cls = np.full((H, W), 255, np.uint8)
    thumb = np.zeros(((max(tys) - ty0 + 1) * 256, (max(txs) - tx0 + 1) * 256, 3), np.uint8)
    for tx, ty in tiles:
        t = classify_tile(tx, ty, ctx)
        cls[(ty - ty0) * g : (ty - ty0 + 1) * g, (tx - tx0) * g : (tx - tx0 + 1) * g] = t["cls"]
        thumb[(ty - ty0) * 256 : (ty - ty0 + 1) * 256, (tx - tx0) * 256 : (tx - tx0 + 1) * 256] = t[
            "thumb"
        ]
        print(f"  tile ({tx},{ty}) {ctx['stats']}", flush=True)
    t_class = time.time() - t0

    e0 = DEM_E0 + tx0 * TILE * MAP_RES
    n0 = DEM_N0 - ty0 * TILE * MAP_RES
    cell = P * MAP_RES
    slope = slope_deg_4m(e0, n0, H, W)
    names = [*CLASSES, "unknown"]
    cost_tab = np.array([COST[n] for n in names] + [np.inf] * 251)
    cost = cost_tab[cls] * (1 + (slope / 10.0) ** 2)
    ys, xs = np.mgrid[0:H, 0:W]
    ce, cn = e0 + (xs + 0.5) * cell, n0 - (ys + 0.5) * cell
    d = end - start
    tproj = np.clip(((ce - start[0]) * d[0] + (cn - start[1]) * d[1]) / (d @ d), 0, 1)
    dist = np.hypot(ce - (start[0] + tproj * d[0]), cn - (start[1] + tproj * d[1]))
    blocked = (slope > a.max_slope) | (dist > a.width / 2) | ~np.isfinite(cost)
    cost[blocked] = 1e6

    def rc(pt: np.ndarray) -> tuple[int, int]:
        return int((n0 - pt[1]) / cell), int((pt[0] - e0) / cell)

    path, total = route(cost, rc(start), rc(end))
    on = cls[path[:, 0], path[:, 1]]
    seg = np.hypot(np.diff(path[:, 0]), np.diff(path[:, 1])) * cell
    length = float(seg.sum())
    comp = {n: float(seg[on[1:] == i].sum() / max(length, 1e-9)) for i, n in enumerate(names)}
    straight = float(np.linalg.norm(d))
    OUT.mkdir(parents=True, exist_ok=True)

    from export_evidence import fit_places_to_lonlat

    alon, alat = fit_places_to_lonlat()
    pe, pn = e0 + (path[:, 1] + 0.5) * cell, n0 - (path[:, 0] + 0.5) * cell
    coords = np.stack([np.polyval(alon, pe), np.polyval(alat, pn)], 1).round(7).tolist()
    (OUT / f"{a.name}.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "LineString", "coordinates": coords},
                        "properties": {
                            "name": a.name,
                            "length_m": round(length, 1),
                            "crs": "IAU_2015:49900 lon/lat",
                        },
                    }
                ],
            }
        )
    )
    np.savez_compressed(
        OUT / f"{a.name}.npz",
        cls=cls,
        cost=cost,
        slope=slope,
        path=path,
        origin_e=e0,
        origin_n=n0,
        cell=cell,
    )
    summary = {
        "name": a.name,
        "db_version": ver,
        "tiles": len(tiles),
        **ctx["stats"],
        "classification_seconds": round(t_class, 1),
        "straight_m": round(straight, 1),
        "route_m": round(length, 1),
        "route_class_share": comp,
        "class_cost": COST,
        "max_slope_deg": a.max_slope,
    }
    (OUT / f"{a.name}.json").write_text(json.dumps(summary, indent=2))

    pal = np.vstack([COLOURS, [[90, 90, 90]]]).astype(np.uint8)
    lab = np.where(cls < 5, cls, 4)
    big = np.kron(pal[lab], np.ones((4, 4, 1), np.uint8))[: thumb.shape[0], : thumb.shape[1]]
    over = (0.45 * thumb + 0.55 * big).astype(np.uint8)
    canvas = Image.new("RGB", (thumb.shape[1] * 2 + 30, thumb.shape[0] + 70), (24, 24, 24))
    canvas.paste(Image.fromarray(thumb), (10, 50))
    canvas.paste(Image.fromarray(over), (thumb.shape[1] + 20, 50))
    dr = ImageDraw.Draw(canvas)
    for x0 in (10, thumb.shape[1] + 20):
        pts = [(x0 + (c + 0.5) * 4, 50 + (r + 0.5) * 4) for r, c in path]
        dr.line(pts, fill=(255, 255, 255), width=3)
        (r_a, c_a), (r_b, c_b) = rc(start), rc(end)
        dr.line(
            [(x0 + c_a * 4, 50 + r_a * 4), (x0 + c_b * 4, 50 + r_b * 4)],
            fill=(255, 0, 255),
            width=1,
        )
    dr.text(
        (10, 12),
        f"{a.name}: route {length:.0f} m (straight {straight:.0f} m), "
        f"{len(tiles)} tiles: {ctx['stats']['computed']} computed, "
        f"{ctx['stats']['cached']} from cache, {t_class:.0f} s",
        font=font(20),
        fill=(255, 255, 255),
    )
    canvas.save(ROOT / f"review/terrain_seg/corridor_{a.name}.png")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
