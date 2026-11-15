"""Export the evidence grid to a COG in the Jezero site CRS (the grid Person A's L2 layers use).

Source grid is in PLACES easting/northing metres; the mapping to lon/lat is fitted on the L3 poses
(both coordinates are stored there; residuals are printed). Target: common.crs.site_crs(jezero),
same cell size, nearest-neighbour sampling.

Bands (float32, nodata -9999): mean probability of soil, bedrock, sand, big_rock; evidence weight;
observation count. The decision rule is NOT applied here, so it can change downstream.

Output: $MSV_DATA/work/terrain_seg/evidence/<area>/terrain_evidence_jezero.tif (not published: a new
rover layer needs a `contract` issue first), checked with common.validate.

Usage: python ml/terrain_seg/export_evidence.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from affine import Affine
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parent))


from common.cog import write_cog  # noqa: E402
from common.crs import geographic, site_crs  # noqa: E402
from common.sites import load_site  # noqa: E402
from common.validate import print_report, validate  # noqa: E402

DATA = Path.home() / "mars-data"
EVIDENCE = DATA / "work/terrain_seg/evidence"
NODATA = -9999.0
CLASSES = ["soil", "bedrock", "sand", "big_rock"]


def fit_places_to_lonlat() -> tuple[np.ndarray, np.ndarray]:
    p = pd.read_parquet(
        DATA / "rover/l3_poses/jezero/v1/poses.parquet",
        columns=["places_easting_m", "places_northing_m", "lon", "lat"],
    )
    a = np.polyfit(p.places_easting_m, p.lon, 1)
    b = np.polyfit(p.places_northing_m, p.lat, 1)
    res_lon = (np.polyval(a, p.places_easting_m) - p.lon).abs().max() * 111_000
    res_lat = (np.polyval(b, p.places_northing_m) - p.lat).abs().max() * 59_000
    print(f"PLACES -> lon/lat linear fit, max residual ~{max(res_lon, res_lat):.3f} m")
    return a, b


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--area", default="landing")
    area = ap.parse_args().area
    ev = np.load(EVIDENCE / area / "evidence.npz")
    out = EVIDENCE / area / "terrain_evidence_jezero.tif"
    cell = float(ev["cell"])
    e0, n0 = float(ev["origin_e"]), float(ev["origin_n"])
    count, weight, sum_p = ev["count"], ev["weight"], ev["sum_p"]
    h, w = count.shape
    a, b = fit_places_to_lonlat()
    site = load_site("jezero")
    crs = site_crs(site)
    to_xy = Transformer.from_crs(geographic(), crs, always_xy=True)
    to_ll = Transformer.from_crs(crs, geographic(), always_xy=True)

    ce = np.array([e0, e0 + w * cell])
    cn = np.array([n0 - h * cell, n0])
    lon = np.polyval(a, np.repeat(ce, 2))
    lat = np.polyval(b, np.tile(cn, 2))
    x, y = to_xy.transform(lon, lat)
    x0, x1 = np.floor(min(x) / cell) * cell, np.ceil(max(x) / cell) * cell
    y0, y1 = np.floor(min(y) / cell) * cell, np.ceil(max(y) / cell) * cell
    W, H = int(round((x1 - x0) / cell)), int(round((y1 - y0) / cell))
    xs = x0 + (np.arange(W) + 0.5) * cell
    ys = y1 - (np.arange(H) + 0.5) * cell
    gx, gy = np.meshgrid(xs, ys)
    glon, glat = to_ll.transform(gx, gy)
    ge = (glon - a[1]) / a[0]
    gn = (glat - b[1]) / b[0]
    col = np.floor((ge - e0) / cell).astype(int)
    row = np.floor((n0 - gn) / cell).astype(int)
    inside = (col >= 0) & (col < w) & (row >= 0) & (row < h)
    col, row = np.clip(col, 0, w - 1), np.clip(row, 0, h - 1)
    obs = inside & (count[row, col] > 0)

    mean_p = sum_p / np.maximum(weight, 1e-9)
    bands = np.full((6, H, W), NODATA, np.float32)
    for c in range(4):
        bands[c][obs] = mean_p[c][row, col][obs]
    bands[4][obs] = weight[row, col][obs]
    bands[5][obs] = count[row, col][obs]
    tags = {
        "MSV_KIND": "terrain_evidence",
        "UNITS": "probability (bands 1-4)",
        "BANDS": ",".join([f"p_{c}" for c in CLASSES] + ["weight", "count"]),
        "SOURCE": "AI4Mars SegFormer on Perseverance Navcam, ray-cast on PLACES DEM",
        "DECISION_RULE_SUGGESTED": "argmax with sand >= 0.55 else next class",
    }
    write_cog(out, bands, Affine(cell, 0, x0, 0, -cell, y1), crs, NODATA, tags)
    print(f"{W}x{H} cells of {cell} m, {obs.sum():,} observed")
    print_report(validate(str(out)))


if __name__ == "__main__":
    main()
