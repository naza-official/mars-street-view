"""L3 Rover poses — build the per-site pose table.

Input  : $MSV_DATA/raw/l3_poses/  (PLACES best_interp.csv, MMGIS waypoints)
         $MSV_DATA/raw/l4_streetview/<stop_id>/**/*.xml  (image labels, attitude fallback only)
Output : $MSV_DATA/rover/l3_poses/<site>/vN/   (mirror of the bucket layout, docs/contracts.md)
           poses.parquet         GeoParquet, one row per (site, drive)   [contract: normalised]
           poses.csv             plan's pose columns only
           crosscheck_mmgis.csv  PLACES vs MMGIS per waypoint
           report.json           build statistics
           layer.json            schema 1.0, written + validated through common/

Conventions (Shared foundations):
  * height_above_sphere_m = PLACES `radius` - 3,396,190 m (no areoid involved)
  * position_xyz_m        = Mars-fixed body-centred XYZ on that sphere
  * roll/pitch/yaw        = PLACES: rover frame vs local level (north-east-down), degrees,
                            yaw 0 = north, clockwise
  * attitude_quat_ned     = PLACES quaternion, rover -> local NED, [x, y, z, w]
  * attitude_quat         = rover -> Mars-fixed, [x, y, z, w] (contract order, as the stop record)

PLACES leaves attitude at identity for site 1 (landing, sols 0-12). For such rows the attitude is
taken from the ROVER_NAV_FRAME quaternion in an image label of the same site/drive when one is on
disk; pose_source then reads "PLACES+label_attitude".
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from common.sites import load_site

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_common.mars import (  # noqa: E402
    R_MARS_SPHERE_M,
    height_above_sphere,
    lonlat_h_to_xyz,
    quat_to_rpy_deg,
    rover_quat_ned_to_mars_fixed,
    to_xyzw,
)
from b_common.pds import data_root  # noqa: E402
from b_common.publish import contract_sources, layer_dir, write_layer  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
VERSION = "v1"

CONTRACT_COLS = ["site", "drive", "lon", "lat", "height", "roll", "pitch", "yaw", "source"]

# Mars 2000 sphere as PROJJSON for GeoParquet metadata (IAU_2015:49900 uses this radius).
MARS_CRS = {
    "$schema": "https://proj.org/schemas/v0.7/projjson.schema.json",
    "type": "GeographicCRS",
    "name": "Mars (2015) - Sphere / Ocentric",
    "datum": {
        "type": "GeodeticReferenceFrame",
        "name": "Mars (2015) - Sphere",
        "ellipsoid": {"name": "Mars (2015) - Sphere", "radius": R_MARS_SPHERE_M},
    },
    "coordinate_system": {
        "subtype": "ellipsoidal",
        "axis": [
            {
                "name": "Ocentric longitude",
                "abbreviation": "lon",
                "direction": "east",
                "unit": "degree",
            },
            {
                "name": "Ocentric latitude",
                "abbreviation": "lat",
                "direction": "north",
                "unit": "degree",
            },
        ],
    },
    "id": {"authority": "IAU_2015", "code": 49900},
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def label_rover_attitude(xml_path: Path):
    """(site, drive, [w,x,y,z]) of ROVER_NAV_FRAME relative to SITE_FRAME, from a PDS4 label."""
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return None
    for csd in root.iter():
        if _local(csd.tag) != "Coordinate_Space_Definition":
            continue
        present = ref = None
        idx = {}
        q = {}
        for el in csd.iter():
            t = _local(el.tag)
            if t == "Coordinate_Space_Present":
                for sub in el.iter():
                    st = _local(sub.tag)
                    if st == "coordinate_space_frame_type":
                        present = sub.text
                    elif st == "index_id":
                        cur = sub.text
                    elif st == "index_value_number":
                        idx[cur] = int(float(sub.text))
            elif t == "Coordinate_Space_Reference":
                for sub in el.iter():
                    if _local(sub.tag) == "coordinate_space_frame_type":
                        ref = sub.text
            elif t in ("qcos", "qsin1", "qsin2", "qsin3"):
                q[t] = float(el.text)
        if present == "ROVER_NAV_FRAME" and ref == "SITE_FRAME" and len(q) == 4:
            return (
                idx.get("SITE"),
                idx.get("DRIVE"),
                [q["qcos"], q["qsin1"], q["qsin2"], q["qsin3"]],
            )
    return None


def label_attitudes(l4_raw: Path) -> dict[tuple[int, int], list[float]]:
    out: dict[tuple[int, int], list[float]] = {}
    for p in sorted(l4_raw.glob("m2020_site*_drive*/**/*.xml")):
        m = re.search(r"site(\d+)_drive(\d+)", str(p))
        key = (int(m[1]), int(m[2]))
        if key in out:
            continue
        r = label_rover_attitude(p)
        if r and (r[0], r[1]) == key:
            out[key] = r[2]
    return out


QCOLS = ["quat_s", "quat_v1", "quat_v2", "quat_v3"]


def apply_label_attitudes(d: pd.DataFrame, fallback: dict) -> int:
    """Where PLACES holds the identity placeholder, use the attitude from an image label."""
    n = 0
    for i in d.index:
        key = (int(d.at[i, "site"]), int(d.at[i, "drive"]))
        q = d.loc[i, QCOLS].to_numpy(float)
        if key in fallback and np.allclose(q, [1, 0, 0, 0], atol=1e-9):
            d.loc[i, QCOLS] = fallback[key]
            d.loc[i, ["roll", "pitch", "yaw"]] = quat_to_rpy_deg(fallback[key])
            d.at[i, "source"] = "PLACES+label_attitude"
            n += 1
    return n


def wkb_points(lon, lat) -> list[bytes]:
    return [struct.pack("<BIdd", 1, 1, x, y) for x, y in zip(lon, lat, strict=False)]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--rover-config", default=str(REPO / "configs/rover/jezero.yaml"))
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.rover_config))
    site = load_site(cfg["site"])

    raw = data_root() / "raw" / "l3_poses"
    out = layer_dir("l3_poses", site.name, VERSION)

    places = pd.read_csv(raw / "places/data_localizations/best_interp.csv")
    d = places[places.frame == "ROVER"].sort_values(["site", "drive"]).reset_index(drop=True)
    d["source"] = "PLACES"
    n_fb = apply_label_attitudes(d, label_attitudes(data_root() / "raw" / "l4_streetview"))

    # best_interp has no drive-0 rows: a new SITE frame is defined at the rover's position, so
    # drive 0 = the SITE origin row. Its quaternion is the (level, north) frame, not the rover;
    # the rover attitude is carried over from the previous site's last drive when the rover had
    # not moved (same position within 1 cm). Otherwise it stays unknown.
    sites = places[places.frame == "SITE"].copy()
    sites["drive"] = 0
    sites["source"] = "PLACES_site_origin"
    sites["sol"] = np.nan
    for i, srow in sites.iterrows():
        prev = d[d.site < srow.site].tail(1)
        if (
            not prev.empty
            and np.hypot(prev.easting.iloc[0] - srow.easting, prev.northing.iloc[0] - srow.northing)
            < 0.01
        ):
            p = prev.iloc[0]
            for col in [
                "roll",
                "pitch",
                "yaw",
                "quat_s",
                "quat_v1",
                "quat_v2",
                "quat_v3",
                "sol",
                "sclk",
            ]:
                sites.at[i, col] = p[col]
            sites.at[i, "source"] = "PLACES_site_origin+prev_attitude"
        else:
            for col, v in zip(
                ["quat_s", "quat_v1", "quat_v2", "quat_v3"], [1.0, 0.0, 0.0, 0.0], strict=False
            ):
                sites.at[i, col] = v
    have = set(zip(d.site, d.drive, strict=False))
    sites = sites[[(s, 0) not in have for s in sites.site]]
    d = (
        pd.concat([d, sites], ignore_index=True)
        .sort_values(["site", "drive"])
        .reset_index(drop=True)
    )

    d = d.rename(columns={"planetocentric_latitude": "lat", "longitude": "lon"})
    d["height_above_sphere_m"] = height_above_sphere(d["radius"])

    q_ned = d[QCOLS].to_numpy(float)
    d["attitude_unknown"] = np.isclose(q_ned, [1, 0, 0, 0], atol=1e-9).all(axis=1)
    d["attitude_quat_ned"] = [to_xyzw(q) for q in q_ned]

    xyz = lonlat_h_to_xyz(d.lon.to_numpy(), d.lat.to_numpy(), d.height_above_sphere_m.to_numpy())
    d["x_m"], d["y_m"], d["z_m"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    d["attitude_quat"] = [
        to_xyzw(rover_quat_ned_to_mars_fixed(q, lo, la))
        for q, lo, la in zip(q_ned, d.lon, d.lat, strict=True)
    ]

    w0, s0, e0, n0 = site.bbox_lonlat
    d["in_site_bounds"] = d.lon.between(w0, e0) & d.lat.between(s0, n0)
    d["stop_id"] = [
        f"{cfg['mission_prefix']}_site{s}_drive{dr}" for s, dr in zip(d.site, d.drive, strict=False)
    ]

    keep = [
        "stop_id",
        "site",
        "drive",
        "sol",
        "sclk",
        "lon",
        "lat",
        "height_above_sphere_m",
        "elevation",
        "areoid",
        "radius",
        "easting",
        "northing",
        "x_m",
        "y_m",
        "z_m",
        "roll",
        "pitch",
        "yaw",
        "attitude_quat_ned",
        "attitude_quat",
        "attitude_unknown",
        "source",
        "in_site_bounds",
    ]
    d = d[keep].rename(
        columns={
            "elevation": "elevation_above_areoid_m",
            "areoid": "areoid_radius_m",
            "radius": "radius_m",
            "easting": "places_easting_m",
            "northing": "places_northing_m",
        }
    )
    d = d.sort_values(["site", "drive"]).reset_index(drop=True)

    # --- GeoParquet
    tbl = pa.Table.from_pandas(d, preserve_index=False)
    tbl = tbl.append_column("geometry", pa.array(wkb_points(d.lon, d.lat), pa.binary()))
    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Point"],
                "crs": MARS_CRS,
                "bbox": [d.lon.min(), d.lat.min(), d.lon.max(), d.lat.max()],
            }
        },
    }
    tbl = tbl.replace_schema_metadata(
        {**(tbl.schema.metadata or {}), b"geo": json.dumps(geo).encode()}
    )
    pq.write_table(tbl, out / "poses.parquet")

    # --- contract CSV
    c = d.rename(columns={"height_above_sphere_m": "height"})[CONTRACT_COLS[:-1] + ["source"]]
    c.to_csv(out / "poses.csv", index=False, float_format="%.6f")

    # --- cross-check against MMGIS waypoints
    wp = json.load(open(raw / "mmgis/M20_waypoints.json"))["features"]
    w = pd.DataFrame([f["properties"] for f in wp])
    # MMGIS `radius` is 0 on many recent waypoints; elev_radii is already radius - 3,396,190 m.
    w = w.replace(-9999.0, np.nan)  # MMGIS nodata
    w["mmgis_h"] = w["elev_radii"]
    j = w.merge(d, on=["site", "drive"], how="left", suffixes=("_mmgis", ""))
    j["d_horiz_m"] = np.hypot(j.easting - j.places_easting_m, j.northing - j.places_northing_m)
    j["d_height_m"] = j.mmgis_h - j.height_above_sphere_m  # differs by MMGIS's areoid radius
    j["d_elev_areoid_m"] = j.elev_geoid - j.elevation_above_areoid_m  # same quantity in both
    j["d_yaw_deg"] = np.where(
        j.attitude_unknown.astype(bool),
        np.nan,  # noqa: E712
        ((j.yaw_mmgis - j.yaw + 180) % 360) - 180,
    )
    j[
        ["site", "drive", "sol_mmgis", "d_horiz_m", "d_elev_areoid_m", "d_height_m", "d_yaw_deg"]
    ].to_csv(out / "crosscheck_mmgis.csv", index=False, float_format="%.4f")

    matched = j.d_horiz_m.notna()
    report = {
        "rows": int(len(d)),
        "sites": int(d.site.nunique()),
        "sol_max": int(d.sol.max()),
        "rows_in_site_bounds": int(d.in_site_bounds.sum()),
        "attitude_from_labels": n_fb,
        "attitude_unknown": int(d.attitude_unknown.sum()),
        "mmgis_waypoints": int(len(w)),
        "mmgis_matched": int(matched.sum()),
        "mmgis_horiz_p50_m": float(j.d_horiz_m[matched].median()),
        "mmgis_horiz_p95_m": float(j.d_horiz_m[matched].quantile(0.95)),
        "mmgis_horiz_max_m": float(j.d_horiz_m[matched].max()),
        "mmgis_elev_areoid_max_abs_m": float(j.d_elev_areoid_m[matched].abs().max()),
        "mmgis_height_p50_m": float(j.d_height_m[matched].median()),
        "mmgis_height_p95_abs_m": float(j.d_height_m[matched].abs().quantile(0.95)),
        "mmgis_unmatched_beyond_places_sol": int((~matched & (w.sol > d.sol.max())).sum()),
        "mmgis_unmatched_other": int((~matched & (w.sol <= d.sol.max())).sum()),
        "rows_site_origin": int(d.source.str.startswith("PLACES_site_origin").sum()),
        "mmgis_yaw_p95_abs_deg": float(j.d_yaw_deg[matched].abs().quantile(0.95)),
    }

    (out / "report.json").write_text(json.dumps(report, indent=2))
    write_layer(
        out,
        "l3_poses",
        site.name,
        VERSION,
        [float(d.lon.min()), float(d.lat.min()), float(d.lon.max()), float(d.lat.max())],
        {
            "poses": "poses.parquet",
            "poses_csv": "poses.csv",
            "crosscheck": "crosscheck_mmgis.csv",
            "report": "report.json",
        },
        contract_sources([raw / "fetch_manifest.json"]),
        notes="One row per (site, drive). attitude_quat = rover -> Mars-fixed, [x, y, z, w]. "
        "Heights = PLACES radius - 3396190 m.",
    )
    print(json.dumps(report, indent=2))
    print("->", out)


if __name__ == "__main__":
    main()
