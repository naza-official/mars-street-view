"""Cut a square window of the PLACES orbital map (0.25 m, 3 bands) around an evidence area.

Only the needed rows are requested with HTTP Range (rows are contiguous in the BSQ file), so a
400 m window costs ~95 MB per band instead of the 3.9 GB file. The window is centred on the
observation-weighted centre of evidence/<name>/evidence.npz.

Output: $MSV_DATA/raw/places_map_window/<name>.npz (b0, b1, b2, origin_e, origin_n, res)

Usage: python ml/terrain_seg/fetch_map_window.py --name seitah --half 200
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import requests

DATA = Path.home() / "mars-data"
URL = ("https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_rover_places/"
       "data_maps/m20_orbital_map.img")
E0, N0, RES, LINES, SAMPLES, HEADER = 4340890.0, 1097070.0, 0.25, 21920, 59200, 118400


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--half", type=float, default=200.0, help="half side of the window, m")
    a = ap.parse_args()
    out = DATA / "raw/places_map_window" / f"{a.name}.npz"
    if out.exists():
        print("exists:", out)
        return
    z = np.load(DATA / "work/terrain_seg/evidence" / a.name / "evidence.npz")
    cell = float(z["cell"])
    cnt = z["count"].astype(float)
    rows, cols = np.nonzero(cnt)
    w = cnt[rows, cols]
    ce = float(z["origin_e"]) + (np.average(cols, weights=w) + 0.5) * cell
    cn = float(z["origin_n"]) - (np.average(rows, weights=w) + 0.5) * cell
    c0, c1 = int((ce - a.half - E0) / RES), int((ce + a.half - E0) / RES)
    r0, r1 = int((N0 - (cn + a.half)) / RES), int((N0 - (cn - a.half)) / RES)
    bands = {}
    for b in range(3):
        start = HEADER + b * LINES * SAMPLES + r0 * SAMPLES
        end = HEADER + b * LINES * SAMPLES + r1 * SAMPLES - 1
        r = requests.get(URL, headers={"Range": f"bytes={start}-{end}",
                                       "User-Agent": "mars-street-view/0.1 (research)"},
                         timeout=1800)
        if r.status_code != 206:
            raise SystemExit(f"range request refused: HTTP {r.status_code}")
        bands[f"b{b}"] = np.frombuffer(r.content, np.uint8).reshape(r1 - r0, SAMPLES)[:, c0:c1]
        print(f"  band {b}: {bands[f'b{b}'].shape}")
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, **bands, origin_e=E0 + c0 * RES, origin_n=N0 - r0 * RES, res=RES)
    print("->", out)


if __name__ == "__main__":
    main()
