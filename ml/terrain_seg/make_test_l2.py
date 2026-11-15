"""Build a TEST stand-in for Person A's L2 layer, in exactly the contract format, from PLACES data.

Purpose: exercise the route service's L2 path (layer.json -> COG windows) before Person A's real
orbital/l2_regional/jezero/vN exists. Not a product: written under $MSV_DATA/test_orbital/, marked
as a test in layer.json notes, never uploaded.

  ortho.tif  uint8, 3 bands, 0.25 m, nodata 0           (PLACES orbital map, resampled)
  dtm.tif    float32, 1 m, metres above the 3,396,190 m sphere, nodata -32768, MSV_KIND=elevation
             (PLACES DEM heights above the areoid + (areoid radius - sphere radius) from L3 poses)
  layer.json schema 1.0, owner A, layer l2_regional, crs IAU_2015:49900, checked by common.validate
Grid: the Jezero site CRS (configs/sites/jezero.yaml), north-up.

Usage: python ml/terrain_seg/make_test_l2.py --lon 77.43 77.46 --lat 18.43 18.45
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from affine import Affine

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import DATA, PlacesSource  # noqa: E402

from common import layerjson  # noqa: E402
from common.cog import write_cog  # noqa: E402
from common.crs import BASE_CRS_CODE, DATUM_ID, MARS_RADIUS_M, site_crs  # noqa: E402
from common.sites import load_site  # noqa: E402
from common.validate import print_report, validate  # noqa: E402

OUT = DATA / "test_orbital/l2_regional/jezero/v1"


def resample(places: PlacesSource, crs, x0, y1, w, h, res, read):
    """Sample a PLACES product onto a site-CRS grid (nearest), via lon/lat."""
    from pyproj import Transformer

    from common.crs import geographic

    to_ll = Transformer.from_crs(crs, geographic(), always_xy=True)
    xs = x0 + (np.arange(w) + 0.5) * res
    ys = y1 - (np.arange(h) + 0.5) * res
    gx, gy = np.meshgrid(xs, ys)
    lon, lat = to_ll.transform(gx, gy)
    pe, pn = places.to_xy(lon, lat)
    e0, e1, n0, n1 = pe.min() - 2, pe.max() + 2, pn.min() - 2, pn.max() + 2
    src, sres = read(e0, n0, e1, n1)
    col = np.clip(((pe - e0) / sres).astype(int), 0, src.shape[1] - 1)
    row = np.clip(((n1 - pn) / sres).astype(int), 0, src.shape[0] - 1)
    return src[row, col]


def raster_bounds_lonlat(crs, x0, y0, x1, y1) -> list[float]:
    from pyproj import Transformer

    from common.crs import geographic

    to_ll = Transformer.from_crs(crs, geographic(), always_xy=True)
    t = np.linspace(0, 1, 21)
    ex = np.concatenate([x0 + t * (x1 - x0), np.full(21, x1), x1 - t * (x1 - x0), np.full(21, x0)])
    ey = np.concatenate([np.full(21, y0), y0 + t * (y1 - y0), np.full(21, y1), y1 - t * (y1 - y0)])
    lon, lat = to_ll.transform(ex, ey)
    pad = 1e-5
    return [
        float(lon.min() - pad),
        float(lat.min() - pad),
        float(lon.max() + pad),
        float(lat.max() + pad),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lon", nargs=2, type=float, default=[77.430, 77.460])
    ap.add_argument("--lat", nargs=2, type=float, default=[18.428, 18.450])
    a = ap.parse_args()
    site = load_site("jezero")
    crs = site_crs(site)
    from pyproj import Transformer

    from common.crs import geographic

    fwd = Transformer.from_crs(geographic(), crs, always_xy=True)
    xs, ys = fwd.transform(
        [a.lon[0], a.lon[1], a.lon[0], a.lon[1]], [a.lat[0], a.lat[0], a.lat[1], a.lat[1]]
    )
    x0, x1 = np.floor(min(xs)), np.ceil(max(xs))
    y0, y1 = np.floor(min(ys)), np.ceil(max(ys))
    places = PlacesSource()
    OUT.mkdir(parents=True, exist_ok=True)

    r_o = 0.25
    wo, ho = int((x1 - x0) / r_o), int((y1 - y0) / r_o)
    rgb = resample(
        places,
        crs,
        x0,
        y1,
        wo,
        ho,
        r_o,
        lambda e0, n0, e1, n1: (places.read_rgb(e0, n0, e1, n1, 0.25), 0.25),
    )
    write_cog(
        OUT / "ortho.tif",
        np.moveaxis(rgb, -1, 0).copy(),
        Affine(r_o, 0, x0, 0, -r_o, y1),
        crs,
        0,
        {"MSV_KIND": "imagery", "SOURCE": "TEST: PLACES m20_orbital_map"},
    )

    p = pd.read_parquet(
        DATA / "rover/l3_poses/jezero/v1/poses.parquet", columns=["areoid_radius_m"]
    )
    offset = float(p.areoid_radius_m.median() - MARS_RADIUS_M)
    r_d = 1.0
    wd, hd = int((x1 - x0) / r_d), int((y1 - y0) / r_d)
    dem = (
        resample(
            places,
            crs,
            x0,
            y1,
            wd,
            hd,
            r_d,
            lambda e0, n0, e1, n1: (places.read_dem(e0, n0, e1, n1, 1), 1.0),
        )
        + offset
    )
    write_cog(
        OUT / "dtm.tif",
        dem.astype(np.float32),
        Affine(r_d, 0, x0, 0, -r_d, y1),
        crs,
        -32768.0,
        {
            "MSV_KIND": "elevation",
            "UNITS": "m",
            "SOURCE": f"TEST: PLACES m20_orbital_dem, areoid->sphere offset {offset:.1f} m",
        },
    )

    doc = {
        "layer": "l2_regional",
        "site": "jezero",
        "version": "v1",
        "schema": layerjson.SCHEMA_VERSION,
        "owner": "A",
        "crs": BASE_CRS_CODE,
        "datum": DATUM_ID,
        "bounds_lonlat": raster_bounds_lonlat(crs, x0, y0, x1, y1),
        "resolution_m": r_o,
        "assets": {"ortho": "ortho.tif", "dtm": "dtm.tif"},
        "depends_on": [],
        "sources": [
            {"url": PlacesSource.URL, "fetched": "2026-11-15", "license": "NASA PDS, public domain"}
        ],
        "validated": True,
        "built_with": "ml/terrain_seg/make_test_l2.py",
        "notes": "TEST LAYER built by Person B from PLACES data to exercise the L2 read path. "
        "Not Person A's product; do not publish.",
    }
    layerjson.write(OUT / "layer.json", doc)
    for f in ("ortho.tif", "dtm.tif"):
        print_report(validate(str(OUT / f)))
    print_report(validate(str(OUT)))
    print(f"ortho {wo}x{ho} @ {r_o} m, dtm {wd}x{hd} @ {r_d} m, areoid->sphere {offset:.1f} m")


if __name__ == "__main__":
    main()
