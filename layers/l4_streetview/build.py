"""L4 Street view — build street-view nodes (panorama + stop record; meshes in a later step).

Input  : $MSV_DATA/raw/l4_streetview/<stop_id>/mosaic/   (Navcam CYL mosaics + labels)
         $MSV_DATA/out/l3_poses/<v>/poses_jezero.parquet   (rover poses, via its layer.json)
Output : $MSV_DATA/out/l4_streetview/<version>/
           stops.geojson                    stop graph (one Feature per stop = the stop record)
           <stop_id>/stop.json              the stop record (contract between A and B)
           <stop_id>/pano/pano.json         panorama metadata (frame, camera position, levels)
           <stop_id>/pano/equirect.png      full 360x180 RGBA, transparent where not imaged
           <stop_id>/pano/preview.jpg       2048 px wide preview
           <stop_id>/pano/<level>/<face>_<y>_<x>.jpg   multi-resolution cubemap tiles
           layer.json

Panorama source: the radiometric cylindrical Navcam mosaic N_LRGB_*_RAS_*_CYL_S_UNCORCL* (colour,
tone-mapped from spectral radiance). Its projection origin (camera position in the SITE frame) is
carried into pano.json and into Mars-fixed XYZ, so the viewer can put the eye at the right point.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from PIL import Image

from common.crs import METRES_PER_DEGREE
from common.sites import load_site

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mesh as M  # noqa: E402
import pano as P  # noqa: E402
from b_common.mars import R_MARS_SPHERE_M, ned_to_mars_fixed_matrix  # noqa: E402
from b_common.pds import data_root  # noqa: E402
from b_common.pds4 import cylindrical, read_array  # noqa: E402
from b_common.publish import contract_sources, latest_layer, layer_dir, write_layer  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
VERSION = "v1"
LAYER = "l4_streetview"
PANO_PREFERENCE = [
    "RAS_{code}_CYL_S_UNCORCL",
    "RAS_{code}_CYL_S_UNCORCT",
    "RAS_{code}_CYL_S_UNCORCG",
    "RAS_{code}_CYL_S_HRZTILS",
    "RAS_{code}_CYL_S_",
]


def pick_pano(mosaic_dir: Path, code: str) -> Path:
    for pat in PANO_PREFERENCE:
        hits = sorted(mosaic_dir.glob(f"N_LRGB_*_{pat.format(code=code)}*.xml"))
        hits = [h for h in hits if h.with_suffix(".IMG").exists()]
        if hits:
            return hits[-1]
    raise FileNotFoundError(f"no cylindrical RAS panorama for {code} in {mosaic_dir}")


def build_stop(
    stop_dir: Path,
    out_dir: Path,
    poses: pd.DataFrame,
    cfg: dict,
    face_size: int,
    mesh_keep: float = 0.25,
    site_name: str = "jezero",
) -> dict:
    m = re.match(r"\w+_site(\d+)_drive(\d+)$", stop_dir.name)
    site, drive = int(m[1]), int(m[2])
    code = f"{site:03d}{drive:04d}"
    stop_id = stop_dir.name
    lbl = pick_pano(stop_dir / "mosaic", code)
    print(f"== {stop_id}: {lbl.name}")

    cyl = cylindrical(lbl)
    rad = read_array(lbl)
    rgba = P.tone_map(rad)
    eq = P.cyl_to_equirect(rgba, cyl.pixel_scale_deg, cyl.zero_elevation_line, cyl.start_azimuth)

    pano_dir = out_dir / stop_id / "pano"
    pano_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(eq).save(pano_dir / "equirect.png", optimize=True)
    eq_rgb = P.fill_sky(eq)
    prev = Image.fromarray(eq_rgb)
    prev.resize((2048, 1024), Image.LANCZOS).save(pano_dir / "preview.jpg", quality=85)
    face = face_size or int(2 ** round(np.log2(90 * cyl.pixel_scale_deg)))  # ~native resolution
    cube = P.write_cubemap_tiles(eq_rgb, pano_dir, face)

    # --- pose: rover pose (L3) for this site/drive; camera = SITE origin + projection origin offset
    row = poses[(poses.site == site) & (poses.drive == drive)]
    if row.empty:
        sys.exit(f"no L3 pose for site {site} drive {drive}")
    r = row.iloc[0]
    origin = poses[(poses.site == site) & (poses.drive == 0)].iloc[0]  # SITE frame origin
    site_xyz = np.array([origin.x_m, origin.y_m, origin.z_m])
    ned = np.array(cyl.origin_site_ned_m)
    cam_xyz = site_xyz + ned_to_mars_fixed_matrix(origin.lon, origin.lat) @ ned
    cam_r = np.linalg.norm(cam_xyz)
    cam = {
        "frame_note": "SITE frame: x north, y east, z down, origin = site origin",
        "position_site_ned_m": list(ned),
        "position_xyz_m": cam_xyz.round(4).tolist(),
        "lon_e": float(np.degrees(np.arctan2(cam_xyz[1], cam_xyz[0])) % 360),
        "lat_n": float(np.degrees(np.arcsin(cam_xyz[2] / cam_r))),
        "height_above_sphere_m": float(cam_r - R_MARS_SPHERE_M),
    }
    pano_meta = {
        "stop_id": stop_id,
        "source_product": lbl.stem,
        "source_url": "https://pds-imaging.jpl.nasa.gov/data/mars2020/mars2020_navcam_ops_mosaic/",
        "frame": "local level at camera; azimuth clockwise from north, elevation up",
        "azimuth_of_equirect_col0_deg": 0.0,
        "coverage_elevation_deg": [cyl.min_elevation, cyl.max_elevation],
        "pixel_scale_px_per_deg": cyl.pixel_scale_deg,
        "camera": cam,
        "equirect": {
            "file": "equirect.png",
            "width": eq.shape[1],
            "height": eq.shape[0],
            "alpha": "0 = not imaged (sky above max elevation, gaps)",
        },
        "preview": "preview.jpg",
        "cubemap": cube,
        "tone_map": "spectral radiance, one white point (99.5th pct, all bands), gamma 2.2",
    }
    P.write_json(pano_dir / "pano.json", pano_meta)

    # --- stop mesh: every colour Navcam mesh of the stop (NLFC/NRFC wedges cover 360 deg out to
    # ~100-180 m; NLG/NRG are greyscale duplicates, NLF_/NRF_ near-field, N?FT one tiled wedge)
    mesh_rel = None
    mesh_dir = stop_dir / "mesh"
    objs = (
        sorted(mesh_dir.glob(f"NLFC*N{code}*.obj")) or sorted(mesh_dir.glob(f"NRFC*N{code}*.obj"))
        if mesh_dir.exists()
        else []
    )
    if objs:
        stop_xyz = np.array([r.x_m, r.y_m, r.z_m])
        mdir = out_dir / stop_id / "mesh"
        info = M.objs_to_stop_glb(
            objs,
            mdir / "stop.glb",
            site_xyz,
            (origin.lon, origin.lat),
            stop_xyz,
            (r.lon, r.lat),
            keep=mesh_keep,
        )
        P.write_json(mdir / "mesh.json", info | {"frame": "stop ENU (z up) in tileset; glTF y-up"})
        M.write_tileset(
            out_dir / stop_id / "tileset.json",
            "mesh/stop.glb",
            stop_xyz,
            (r.lon, r.lat),
            info["bbox_enu_min"],
            info["bbox_enu_max"],
        )
        mesh_rel = f"rover/{LAYER}/{site_name}/{VERSION}/{stop_id}/tileset.json"
        print(
            f"   mesh {len(objs)} wedges: {info['triangles']:,} tris, "
            f"bbox {info['bbox_enu_min']}..{info['bbox_enu_max']}"
        )

    prefix = cfg["mission_prefix"]
    stop = {
        "stop_id": stop_id,
        "rover": cfg["rover"],
        "site": site,
        "drive": drive,
        "sol": int(re.match(r"N_\w{4}_(\d{4})", lbl.name)[1]),  # sol the panorama was taken
        "pose_sol": int(r.sol) if pd.notna(r.sol) else None,
        "position_xyz_m": [round(float(r.x_m), 4), round(float(r.y_m), 4), round(float(r.z_m), 4)],
        "lon_e": float(r.lon),
        "lat_n": float(r.lat),
        "height_above_sphere_m": float(r.height_above_sphere_m),
        "attitude_quat": [
            round(float(v), 9) for v in r.attitude_quat
        ],  # [x, y, z, w], rover -> Mars-fixed
        "pose_source": r.source,
        "alignment_rms_m": None,  # set after pc_align against A's L2 Jezero terrain
        "panorama": f"rover/{LAYER}/{site_name}/{VERSION}/{stop_id}/pano/",
        "mesh_tileset": mesh_rel,
        "next_stops": [],
    }
    assert stop_id.startswith(prefix)
    P.write_json(out_dir / stop_id / "stop.json", stop)
    return stop


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--rover-config", default=str(REPO / "configs/rover/jezero.yaml"))
    ap.add_argument("--stop", action="append", help="stop_id(s); default: every stop with raw data")
    ap.add_argument("--face-size", type=int, default=0, help="cube face px (default ~ native)")
    ap.add_argument(
        "--mesh-keep", type=float, default=0.10, help="fraction of mesh triangles to keep"
    )
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.rover_config))
    site = load_site(cfg["site"])

    raw = data_root() / "raw" / LAYER
    out = layer_dir(LAYER, site.name, VERSION)
    l3 = latest_layer("l3_poses", site.name)
    l3_doc = json.loads((l3 / "layer.json").read_text())
    poses = pd.read_parquet(l3 / l3_doc["assets"]["poses"])

    prefix = cfg["mission_prefix"]
    stop_dirs = [raw / s for s in a.stop] if a.stop else sorted(raw.glob(f"{prefix}_site*_drive*"))
    stops = [build_stop(d, out, poses, cfg, a.face_size, a.mesh_keep, site.name) for d in stop_dirs]

    stops.sort(key=lambda s: (s["site"], s["drive"]))
    for s, nxt in zip(stops, stops[1:], strict=False):
        s["next_stops"] = [nxt["stop_id"]]
        P.write_json(out / s["stop_id"] / "stop.json", s)
    fc = {
        "type": "FeatureCollection",
        "name": "stops",
        "features": [
            {
                "type": "Feature",
                "id": s["stop_id"],
                "geometry": {
                    "type": "Point",
                    "coordinates": [s["lon_e"], s["lat_n"], s["height_above_sphere_m"]],
                },
                "properties": s,
            }
            for s in stops
        ],
    }
    P.write_json(out / "stops.geojson", fc)

    pad = 200.0 / METRES_PER_DEGREE
    lon = [s["lon_e"] for s in stops]
    lat = [s["lat_n"] for s in stops]
    assets = {"stops": "stops.geojson"}
    for s in stops:
        assets[f"{s['stop_id']}_stop"] = f"{s['stop_id']}/stop.json"
        assets[f"{s['stop_id']}_pano"] = f"{s['stop_id']}/pano/pano.json"
        if s["mesh_tileset"]:
            assets[f"{s['stop_id']}_tileset"] = f"{s['stop_id']}/tileset.json"
    write_layer(
        out,
        LAYER,
        site.name,
        VERSION,
        [min(lon) - pad, min(lat) - pad, max(lon) + pad, max(lat) + pad],
        assets,
        contract_sources([d / "fetch_manifest.json" for d in stop_dirs]),
        depends_on=[f"rover/l3_poses/{site.name}/{l3_doc['version']}"],
        notes="Stop records follow docs/contracts.md. attitude_quat = rover -> Mars-fixed, "
        "[x, y, z, w]. Panorama: cubemap tiles {level}/{face}_{y}_{x}.jpg, see pano.json.",
    )
    print("->", out, f"({len(stops)} stops)")


if __name__ == "__main__":
    main()
