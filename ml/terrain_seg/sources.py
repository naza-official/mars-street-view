"""Orbital data sources for the route service: one interface, two implementations.

  L2Source      Person A's L2 layer (orbital/l2_regional/<site>/vN/), found through its layer.json,
                local path or s3:// URI. Assets "ortho" (HiRISE, uint8, 1 or 3 bands) and "dtm"
                (float32, metres above the 3,396,190 m sphere), COGs in the site CRS, read by
                window.
                This is the production source.
  PlacesSource  The Mars 2020 PLACES orbital map + DEM (PDS), in PLACES easting/northing metres.
                Demo / fallback only (not part of the contract).

Interface (all coordinates in the source's own CRS, north-up arrays, row 0 at the top):
  version                         string used in cache keys
  crs                             pyproj CRS, or None for PLACES
  to_xy(lon, lat), to_lonlat(x, y)
  read_rgb(x0, y0, x1, y1, res)   uint8 (H, W, 3); 0 outside the data
  read_dem(x0, y0, x1, y1, res)   float32 (H, W) heights, NaN outside the data
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np
import requests

DATA = Path.home() / "mars-data"


class L2Source:
    def __init__(self, uri: str):
        import rasterio
        from pyproj import CRS, Transformer

        from common import layerjson
        from common.crs import geographic
        from common.validate import Target

        self.root = Target(uri)
        self.doc = self.root.child("layer.json").read_json()
        problems = layerjson.errors(self.doc)
        if problems:
            raise ValueError(f"{uri}: invalid layer.json: {problems}")
        for need in ("ortho", "dtm"):
            if need not in self.doc["assets"]:
                raise ValueError(f"{uri}: layer.json has no '{need}' asset")
        self.version = f"{self.doc['layer']}_{self.doc['site']}_{self.doc['version']}"
        self._env = self.root.gdal_env()
        self._paths = {
            k: self.root.child(self.doc["assets"][k]).raster_path() for k in ("ortho", "dtm")
        }
        with rasterio.Env(**self._env), rasterio.open(self._paths["ortho"]) as src:
            self.crs = CRS.from_wkt(src.crs.to_wkt())
        self._fwd = Transformer.from_crs(geographic(), self.crs, always_xy=True)
        self._inv = Transformer.from_crs(self.crs, geographic(), always_xy=True)

    def to_xy(self, lon, lat):
        return self._fwd.transform(lon, lat)

    def to_lonlat(self, x, y):
        return self._inv.transform(x, y)

    def _read(self, key: str, x0, y0, x1, y1, res, resampling, fill):
        import rasterio
        from rasterio.windows import from_bounds

        h, w = int(round((y1 - y0) / res)), int(round((x1 - x0) / res))
        with rasterio.Env(**self._env), rasterio.open(self._paths[key]) as src:
            win = from_bounds(x0, y0, x1, y1, src.transform)
            data = src.read(
                window=win,
                out_shape=(src.count, h, w),
                boundless=True,
                fill_value=src.nodata if src.nodata is not None else fill,
                resampling=resampling,
            )
            return data, src.nodata

    def read_rgb(self, x0, y0, x1, y1, res):
        from rasterio.enums import Resampling

        data, _ = self._read("ortho", x0, y0, x1, y1, res, Resampling.bilinear, 0)
        data = np.repeat(data, 3, 0) if data.shape[0] == 1 else data[:3]
        return np.moveaxis(data, 0, -1).astype(np.uint8)

    def read_dem(self, x0, y0, x1, y1, res):
        from rasterio.enums import Resampling

        data, nodata = self._read("dtm", x0, y0, x1, y1, res, Resampling.average, np.nan)
        z = data[0].astype(np.float32)
        if nodata is not None:
            z[z == nodata] = np.nan
        return z


class PlacesSource:
    URL = (
        "https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_rover_places/"
        "data_maps/m20_orbital_map.img"
    )
    E0, N0, RES, LINES, SAMPLES, HEADER = 4340890.0, 1097070.0, 0.25, 21920, 59200, 118400

    def __init__(self):
        import pandas as pd

        p = pd.read_parquet(
            DATA / "rover/l3_poses/jezero/v1/poses.parquet",
            columns=["places_easting_m", "places_northing_m", "lon", "lat"],
        )
        self._lon = np.polyfit(p.places_easting_m, p.lon, 1)
        self._lat = np.polyfit(p.places_northing_m, p.lat, 1)
        self.version = "places_m20_orbital_map"
        self.crs = None
        self._dem = np.load(DATA / "work/places_dem.npy", mmap_mode="r")
        self._s = requests.Session()
        self._s.headers["User-Agent"] = "mars-street-view/0.1 (research)"

    def to_xy(self, lon, lat):
        return (
            (np.asarray(lon) - self._lon[1]) / self._lon[0],
            (np.asarray(lat) - self._lat[1]) / self._lat[0],
        )

    def to_lonlat(self, x, y):
        return np.polyval(self._lon, x), np.polyval(self._lat, y)

    def read_rgb(self, x0, y0, x1, y1, res):
        c0, c1 = int((x0 - self.E0) / self.RES), int((x1 - self.E0) / self.RES)
        r0, r1 = int((self.N0 - y1) / self.RES), int((self.N0 - y0) / self.RES)
        rgb = np.zeros((r1 - r0, c1 - c0, 3), np.uint8)
        rr0, rr1 = max(r0, 0), min(r1, self.LINES)
        cc0, cc1 = max(c0, 0), min(c1, self.SAMPLES)
        for b in range(3):
            rgb[rr0 - r0 : rr1 - r0, cc0 - c0 : cc1 - c0, b] = self._rows(
                b, range(rr0, rr1), cc0, cc1
            )
        if res != self.RES:
            from PIL import Image

            h, w = int(round((y1 - y0) / res)), int(round((x1 - x0) / res))
            rgb = np.asarray(Image.fromarray(rgb).resize((w, h), Image.BILINEAR))
        return rgb

    def read_dem(self, x0, y0, x1, y1, res):
        c0, r0 = int(round(x0 - self.E0)), int(round(self.N0 - y1))
        w, h = int(round(x1 - x0)), int(round(y1 - y0))
        z = np.asarray(self._dem[max(r0, 0) : r0 + h, max(c0, 0) : c0 + w], np.float32)
        f = int(round(res))
        hh, ww = (z.shape[0] // f) * f, (z.shape[1] // f) * f
        return z[:hh, :ww].reshape(hh // f, f, ww // f, f).mean((1, 3))

    def _rows(self, band: int, rows: range, c0: int, c1: int) -> np.ndarray:
        out = np.zeros((len(rows), c1 - c0), np.uint8)
        base = self.HEADER + band * self.LINES * self.SAMPLES
        rows = list(rows)
        for i in range(0, len(rows), 200):
            chunk = rows[i : i + 200]
            spans = [
                (base + r * self.SAMPLES + c0, base + r * self.SAMPLES + c1 - 1) for r in chunk
            ]
            hdr = "bytes=" + ",".join(f"{a}-{b}" for a, b in spans)
            for attempt in range(4):
                try:
                    resp = self._s.get(self.URL, headers={"Range": hdr}, timeout=300)
                    if resp.status_code != 206:
                        raise requests.RequestException(f"HTTP {resp.status_code}")
                    parts = _multipart(resp)
                    break
                except requests.RequestException:
                    if attempt == 3:
                        raise
                    time.sleep(3 * (attempt + 1))
            for k, (a, _) in enumerate(spans):
                out[i + k] = np.frombuffer(parts[a], np.uint8)
        return out


def _multipart(resp: requests.Response) -> dict[int, bytes]:
    ctype = resp.headers.get("Content-Type", "")
    if "multipart/byteranges" not in ctype:
        start = int(re.search(r"bytes (\d+)-", resp.headers["Content-Range"]).group(1))
        return {start: resp.content}
    boundary = ctype.split("boundary=")[1].strip().strip('"').encode()
    parts = {}
    for seg in resp.content.split(b"--" + boundary):
        if b"Content-Range" not in seg:
            continue
        head, _, data = seg.partition(b"\r\n\r\n")
        m = re.search(rb"bytes (\d+)-(\d+)/", head)
        a, b = int(m.group(1)), int(m.group(2))
        parts[a] = data[: b - a + 1]
    return parts


def open_source(spec: str):
    """'places' -> PlacesSource; anything else is an L2 layer folder or s3:// URI."""
    return PlacesSource() if spec == "places" else L2Source(spec)
