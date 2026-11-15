"""Rover terrain mesh (MIPL OBJ, SITE frame) -> textured GLB in stop-local ENU + 3D Tiles 1.1.

Frames
  SITE frame  : x north, y east, z down, origin = site origin (MIPL / PLACES)
  stop ENU    : x east, y north, z up, origin = rover position at the stop (L3)
  glTF content: y-up (x = east, y = up, z = -north), as 3D Tiles expects; the tile `transform`
                maps stop ENU (z-up) to Mars-fixed XYZ, so CesiumJS / 3DTilesRendererJS place it.
"""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def read_obj(path: Path):
    """Positions (N,3), uvs (M,2), faces_v (F,3), faces_vt (F,3), 0-based; mtl texture path."""
    v, vt, fv, ft = [], [], [], []
    tex = None
    for line in open(path):
        if line.startswith("v "):
            v.append(line.split()[1:4])
        elif line.startswith("vt "):
            vt.append(line.split()[1:3])
        elif line.startswith("f "):
            parts = [p.split("/") for p in line.split()[1:4]]
            fv.append([p[0] for p in parts])
            ft.append([p[1] if len(p) > 1 and p[1] else p[0] for p in parts])
        elif line.startswith("mtllib "):
            mtl = path.with_name(line.split(maxsplit=1)[1].strip())
            if mtl.exists():
                for ml in open(mtl):
                    if ml.strip().startswith("map_Kd"):
                        tex = path.with_name(ml.split(maxsplit=1)[1].strip())
    return (
        np.array(v, float),
        np.array(vt, float),
        np.array(fv, np.int64) - 1,
        np.array(ft, np.int64) - 1,
        tex,
    )


def enu_matrix(lon_e_deg: float, lat_n_deg: float) -> np.ndarray:
    """Columns = local East, North, Up unit vectors in Mars-fixed XYZ."""
    lo, la = np.radians(lon_e_deg), np.radians(lat_n_deg)
    e = [-np.sin(lo), np.cos(lo), 0.0]
    n = [-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)]
    u = [np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]
    return np.array([e, n, u]).T


def site_to_stop_enu(
    p_site_ned: np.ndarray, site_xyz: np.ndarray, site_lonlat, stop_xyz: np.ndarray, stop_lonlat
) -> np.ndarray:
    """SITE-frame (NED at site origin) points -> stop-local ENU (z up)."""
    r_site = enu_matrix(*site_lonlat)
    enu_site = np.stack([p_site_ned[:, 1], p_site_ned[:, 0], -p_site_ned[:, 2]], axis=1)
    mf = site_xyz + enu_site @ r_site.T
    return (mf - stop_xyz) @ enu_matrix(*stop_lonlat)


def _pad4(b: bytes, fill: bytes = b"\x00") -> bytes:
    return b + fill * ((4 - len(b) % 4) % 4)


def write_glb(path: Path, parts: list[tuple[np.ndarray, np.ndarray, np.ndarray, bytes]]) -> None:
    """parts = [(pos_enu (N,3), uv (N,2), tri (F,3), jpeg)]; one primitive + material each."""
    blobs, views, accessors, prims, materials, textures, images = [], [], [], [], [], [], []
    for pos_enu, uv, idx, jpeg in parts:
        pos = np.stack([pos_enu[:, 0], pos_enu[:, 2], -pos_enu[:, 1]], axis=1).astype(
            np.float32
        )  # y-up
        uvf = np.stack([uv[:, 0], 1.0 - uv[:, 1]], axis=1).astype(
            np.float32
        )  # OBJ v up -> glTF v down
        ix = idx.astype(np.uint32).ravel()
        k = len(blobs)
        for b, tgt in (
            (pos.tobytes(), 34962),
            (uvf.tobytes(), 34962),
            (ix.tobytes(), 34963),
            (jpeg, None),
        ):
            blobs.append(b)
            views.append({"buffer": 0, "byteLength": len(b)} | ({"target": tgt} if tgt else {}))
        a = len(accessors)
        accessors += [
            {
                "bufferView": k,
                "componentType": 5126,
                "count": len(pos),
                "type": "VEC3",
                "min": pos.min(0).tolist(),
                "max": pos.max(0).tolist(),
            },
            {"bufferView": k + 1, "componentType": 5126, "count": len(uvf), "type": "VEC2"},
            {"bufferView": k + 2, "componentType": 5125, "count": len(ix), "type": "SCALAR"},
        ]
        i = len(images)
        images.append({"bufferView": k + 3, "mimeType": "image/jpeg"})
        textures.append({"source": i, "sampler": 0})
        materials.append(
            {
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": i},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
                "extensions": {"KHR_materials_unlit": {}},
            }
        )
        prims.append(
            {
                "attributes": {"POSITION": a, "TEXCOORD_0": a + 1},
                "indices": a + 2,
                "material": i,
                "mode": 4,
            }
        )
    buf = b""
    for v, b in zip(views, blobs, strict=False):
        v["byteOffset"] = len(buf)
        buf += _pad4(b)
    gltf = {
        "asset": {"version": "2.0", "generator": "mars-street-view l4 mesh.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": prims}],
        "materials": materials,
        "extensionsUsed": ["KHR_materials_unlit"],
        "textures": textures,
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
        "images": images,
        "buffers": [{"byteLength": len(buf)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    js = _pad4(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    total = 12 + 8 + len(js) + 8 + len(buf)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(buf), 0x004E4942) + buf)


def decimate(pos: np.ndarray, uv: np.ndarray, tri: np.ndarray, keep: float):
    """Quadric decimation to ~`keep` of the triangles; uvs follow the nearest kept vertex."""
    if keep >= 1.0 or len(tri) < 2000:
        return pos, uv, tri
    import fast_simplification
    from scipy.spatial import cKDTree

    p2, t2 = fast_simplification.simplify(pos.astype(np.float32), tri.astype(np.int32), 1.0 - keep)
    _, nn = cKDTree(pos).query(p2)
    return p2.astype(float), uv[nn], t2


def objs_to_stop_glb(
    objs: list[Path],
    out_glb: Path,
    site_xyz,
    site_lonlat,
    stop_xyz,
    stop_lonlat,
    keep: float = 0.25,
    max_tex: int = 2048,
    quality: int = 85,
) -> dict:
    parts, info = [], {"sources": [], "vertices": 0, "triangles": 0}
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for obj in objs:
        v, vt, fv, ft, tex = read_obj(obj)
        # unweld per (vertex, uv) pair — MIPL OBJ stores one uv per face corner
        pairs = np.stack([fv.ravel(), ft.ravel()], axis=1)
        uniq, inv = np.unique(pairs, axis=0, return_inverse=True)
        pos = site_to_stop_enu(
            v[uniq[:, 0]], np.asarray(site_xyz), site_lonlat, np.asarray(stop_xyz), stop_lonlat
        )
        pos, uv, tri = decimate(pos, vt[uniq[:, 1]], inv.reshape(-1, 3), keep)
        img = Image.open(tex).convert("RGB")
        if max(img.size) > max_tex:
            sc = max_tex / max(img.size)
            img = img.resize((int(img.width * sc), int(img.height * sc)), Image.LANCZOS)
        jb = io.BytesIO()
        img.save(jb, "JPEG", quality=quality)
        parts.append((pos, uv, tri, jb.getvalue()))
        lo, hi = np.minimum(lo, pos.min(0)), np.maximum(hi, pos.max(0))
        info["sources"].append(
            {"obj": obj.name, "triangles_in": int(len(fv)), "triangles_out": int(len(tri))}
        )
        info["vertices"] += int(len(pos))
        info["triangles"] += int(len(tri))
    out_glb.parent.mkdir(parents=True, exist_ok=True)
    write_glb(out_glb, parts)
    info |= {
        "decimation_keep": keep,
        "bbox_enu_min": lo.round(3).tolist(),
        "bbox_enu_max": hi.round(3).tolist(),
    }
    return info


def write_tileset(
    path: Path,
    glb_name: str,
    stop_xyz,
    stop_lonlat,
    bbox_min,
    bbox_max,
    geometric_error: float = 0.05,
) -> None:
    r = enu_matrix(*stop_lonlat)
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = stop_xyz
    c = (np.array(bbox_min) + np.array(bbox_max)) / 2
    h = (np.array(bbox_max) - np.array(bbox_min)) / 2
    ts = {
        "asset": {"version": "1.1", "generator": "mars-street-view l4"},
        "geometricError": 50.0,
        "root": {
            "transform": m.T.ravel().tolist(),
            "boundingVolume": {"box": [*c, h[0], 0, 0, 0, h[1], 0, 0, 0, h[2]]},
            "geometricError": geometric_error,
            "refine": "REPLACE",
            "content": {"uri": glb_name},
        },
    }
    path.write_text(json.dumps(ts, indent=2))
