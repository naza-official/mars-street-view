"""Fetch raw-images metadata (camera models, sols, site/drive) for Navcam frames in a sol range.

The feed is sorted by sol (descending), so the first page holding --sol-max is found by binary
search, then pages are read until sols drop below --sol-min.

Output: $MSV_DATA/raw/navcam_meta/<name>/NAVCAM_{LEFT,RIGHT}_p<page>.json

Usage: python ml/terrain_seg/fetch_navcam_meta.py --name seitah --sol-min 200 --sol-max 260
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

API = ("https://mars.nasa.gov/rss/api/?feed=raw_images&category=mars2020&feedtype=json"
       "&num=100&order=sol+desc&search={cam}&page={page}")
UA = {"User-Agent": "mars-street-view-review/0.1 (research)"}
OUT = Path.home() / "mars-data/raw/navcam_meta"


def page(s: requests.Session, cam: str, p: int) -> list[dict]:
    for attempt in range(4):
        try:
            return s.get(API.format(cam=cam, page=p), timeout=60).json()["images"]
        except (requests.RequestException, ValueError, KeyError):
            time.sleep(2 * (attempt + 1))
    return []


def first_page_with(s: requests.Session, cam: str, sol_max: int, last: int) -> int:
    lo, hi = 0, last
    while lo < hi:
        mid = (lo + hi) // 2
        ims = page(s, cam, mid)
        if ims and min(i["sol"] for i in ims) > sol_max:
            lo = mid + 1
        else:
            hi = mid
    return max(0, lo - 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--sol-min", type=int, required=True)
    ap.add_argument("--sol-max", type=int, required=True)
    ap.add_argument("--cams", nargs="*", default=["NAVCAM_LEFT", "NAVCAM_RIGHT"])
    a = ap.parse_args()
    out = OUT / a.name
    out.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(UA)
    for cam in a.cams:
        total = s.get(API.format(cam=cam, page=0), timeout=60).json()["total_results"]
        p = first_page_with(s, cam, a.sol_max, total // 100)
        n = 0
        while p <= total // 100:
            ims = page(s, cam, p)
            if not ims:
                break
            keep = [i for i in ims if a.sol_min <= i["sol"] <= a.sol_max]
            if keep:
                (out / f"{cam}_p{p}.json").write_text(json.dumps({"images": keep}))
                n += len(keep)
            if max(i["sol"] for i in ims) < a.sol_min:
                break
            p += 1
            time.sleep(0.2)
        print(f"{cam}: {n} frames in sols {a.sol_min}-{a.sol_max}")
    print("->", out)


if __name__ == "__main__":
    main()
