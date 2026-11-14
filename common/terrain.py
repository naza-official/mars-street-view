import json
import struct
from dataclasses import dataclass

import numpy as np
from pydelatin import Delatin

from common.crs import MARS_RADIUS_M, lonlat_to_xyz

QUANT_MAX = 65535
UNSIGNED_SHORT, UNSIGNED_INT = 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
CLAMP_TO_EDGE, LINEAR, LINEAR_MIPMAP_LINEAR = 33071, 9729, 9987
GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = 0x46546C67, 0x4E4F534A, 0x004E4942
EXTENSIONS = ["KHR_mesh_quantization", "KHR_materials_unlit"]


@dataclass(frozen=True)
class Extent:
    west: float
    south: float
    east: float
    north: float

    @property
    def centre(self) -> tuple[float, float]:
        return (self.west + self.east) / 2, (self.south + self.north) / 2

    @property
    def span_deg(self) -> float:
        return max(self.east - self.west, self.north - self.south)


@dataclass(frozen=True)
class TileKey:
    level: int
    row: int
    col: int

    @property
    def name(self) -> str:
        return f"{self.level}/{self.row}/{self.col}"

    def children(self) -> list["TileKey"]:
        return [TileKey(self.level + 1, 2 * self.row + dr, 2 * self.col + dc)
                for dr in (0, 1) for dc in (0, 1)]

    def ancestor(self, level: int) -> "TileKey":
        shift = self.level - level
        return TileKey(level, self.row >> shift, self.col >> shift)


@dataclass(frozen=True)
class TileGrid:
    extent: Extent
    rows0: int
    cols0: int

    def shape(self, level: int) -> tuple[int, int]:
        return self.rows0 << level, self.cols0 << level

    def keys(self, level: int) -> list[TileKey]:
        rows, cols = self.shape(level)
        return [TileKey(level, r, c) for r in range(rows) for c in range(cols)]

    def tile_extent(self, key: TileKey) -> Extent:
        rows, cols = self.shape(key.level)
        dlon = (self.extent.east - self.extent.west) / cols
        dlat = (self.extent.north - self.extent.south) / rows
        west = self.extent.west + key.col * dlon
        north = self.extent.north - key.row * dlat
        return Extent(west, north - dlat, west + dlon, north)


@dataclass(frozen=True)
class Mesh:
    rows: np.ndarray
    cols: np.ndarray
    triangles: np.ndarray
    skirt: np.ndarray


def grid_lonlat(extent: Extent, rows: np.ndarray, cols: np.ndarray,
                segments: int) -> tuple[np.ndarray, np.ndarray]:
    lon = extent.west + cols / segments * (extent.east - extent.west)
    lat = extent.north - rows / segments * (extent.north - extent.south)
    return lon, lat


def up_vector(lon: float, lat: float) -> np.ndarray:
    return lonlat_to_xyz(np.array(lon), np.array(lat), np.array(-MARS_RADIUS_M + 1.0))


def curved_heights(corners: np.ndarray, extent: Extent) -> np.ndarray:
    n = corners.shape[0]
    rows, cols = np.mgrid[0:n, 0:n]
    lon, lat = grid_lonlat(extent, rows, cols, n - 1)
    xyz = lonlat_to_xyz(lon, lat, corners)
    return xyz @ up_vector(*extent.centre) - MARS_RADIUS_M


def grid_triangles(n: int) -> np.ndarray:
    r, c = np.mgrid[0:n - 1, 0:n - 1]
    a = (r * n + c).ravel()
    b, d = a + 1, a + n
    return np.stack([a, d, b, b, d, d + 1], axis=1).reshape(-1, 3).astype(np.uint32)


def regular_mesh(n: int, stride: int = 1) -> Mesh:
    idx = np.arange(0, n, stride)
    if idx[-1] != n - 1:
        idx = np.append(idx, n - 1)
    rows, cols = np.meshgrid(idx, idx, indexing="ij")
    return Mesh(rows.ravel(), cols.ravel(), grid_triangles(len(idx)),
                np.zeros(rows.size, bool))


def simplified_mesh(heights: np.ndarray, max_error: float) -> Mesh:
    n = heights.shape[0]
    tin = Delatin(heights.astype(np.float32), width=n, height=n, max_error=max_error)
    cols = tin.vertices[:, 0].astype(np.int64)
    rows = (n - 1) - tin.vertices[:, 1].astype(np.int64)
    return Mesh(rows, cols, tin.triangles.astype(np.uint32), np.zeros(rows.size, bool))


def boundary_edges(triangles: np.ndarray) -> np.ndarray:
    edges = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    keys = np.sort(edges, axis=1)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    return edges[counts[inverse.ravel()] == 1]


def with_skirts(mesh: Mesh) -> Mesh:
    edges = boundary_edges(mesh.triangles)
    border = np.unique(edges)
    base = len(mesh.rows)
    copy = np.full(base, -1, np.int64)
    copy[border] = base + np.arange(len(border))
    a, b = edges[:, 0], edges[:, 1]
    skirt_tris = np.concatenate([np.stack([a, b, copy[b]], 1), np.stack([a, copy[b], copy[a]], 1)])
    return Mesh(
        np.concatenate([mesh.rows, mesh.rows[border]]),
        np.concatenate([mesh.cols, mesh.cols[border]]),
        np.concatenate([mesh.triangles, skirt_tris]).astype(np.uint32),
        np.concatenate([mesh.skirt, np.ones(len(border), bool)]),
    )


def mesh_xyz(mesh: Mesh, corners: np.ndarray, extent: Extent, skirt_depth: float) -> np.ndarray:
    n = corners.shape[0]
    lon, lat = grid_lonlat(extent, mesh.rows, mesh.cols, n - 1)
    heights = corners[mesh.rows, mesh.cols].astype(np.float64) - skirt_depth * mesh.skirt
    return lonlat_to_xyz(lon, lat, heights)


def mesh_uv(mesh: Mesh, n: int) -> np.ndarray:
    return np.stack([mesh.cols / (n - 1), mesh.rows / (n - 1)], axis=1)


def z_up_to_y_up(xyz: np.ndarray) -> np.ndarray:
    return np.stack([xyz[:, 0], xyz[:, 2], -xyz[:, 1]], axis=1)


def y_up_to_z_up(xyz: np.ndarray) -> np.ndarray:
    return np.stack([xyz[:, 0], -xyz[:, 2], xyz[:, 1]], axis=1)


@dataclass(frozen=True)
class Quantized:
    values: np.ndarray
    offset: np.ndarray
    step: np.ndarray

    @property
    def matrix(self) -> list[float]:
        m = np.diag([*self.step, 1.0])
        m[:3, 3] = self.offset
        return m.T.ravel().tolist()

    @property
    def max_error_m(self) -> float:
        return float(np.linalg.norm(self.step) / 2)


def quantize(points: np.ndarray) -> Quantized:
    low, high = points.min(axis=0), points.max(axis=0)
    step = np.maximum(high - low, 1e-6) / QUANT_MAX
    values = np.round((points - low) / step).astype(np.uint16)
    return Quantized(values, low, step)


def dequantize(q: Quantized) -> np.ndarray:
    return q.values.astype(np.float64) * q.step + q.offset


def _pad4(data: bytes, fill: bytes = b"\0") -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def _views(blobs: list[bytes], targets: list[int | None],
           strides: list[int | None]) -> tuple[list[dict], bytes]:
    views, offset, chunks = [], 0, []
    for blob, target, stride in zip(blobs, targets, strides, strict=True):
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(blob)}
        if target is not None:
            view["target"] = target
        if stride is not None:
            view["byteStride"] = stride
        views.append(view)
        padded = _pad4(blob)
        chunks.append(padded)
        offset += len(padded)
    return views, b"".join(chunks)


def _material(textured: bool) -> dict:
    pbr: dict = {"metallicFactor": 0.0, "roughnessFactor": 1.0}
    if textured:
        pbr["baseColorTexture"] = {"index": 0}
    else:
        pbr["baseColorFactor"] = [0.75, 0.45, 0.3, 1.0]
    return {"pbrMetallicRoughness": pbr, "doubleSided": True,
            "extensions": {"KHR_materials_unlit": {}}}


def gltf_document(q: Quantized, uv_count: int, index_count: int, index_type: int,
                  views: list[dict], buffer_length: int, textured: bool) -> dict:
    doc = {
        "asset": {"version": "2.0", "generator": "mars-sv common.terrain"},
        "extensionsUsed": EXTENSIONS,
        "extensionsRequired": ["KHR_mesh_quantization"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "matrix": q.matrix}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                                    "indices": 2, "material": 0, "mode": 4}]}],
        "materials": [_material(textured)],
        "buffers": [{"byteLength": buffer_length}],
        "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": UNSIGNED_SHORT, "count": len(q.values),
             "type": "VEC3", "min": q.values.min(axis=0).tolist(),
             "max": q.values.max(axis=0).tolist()},
            {"bufferView": 1, "componentType": UNSIGNED_SHORT, "normalized": True,
             "count": uv_count, "type": "VEC2"},
            {"bufferView": 2, "componentType": index_type, "count": index_count,
             "type": "SCALAR"},
        ],
    }
    if textured:
        doc["images"] = [{"bufferView": 3, "mimeType": "image/jpeg"}]
        doc["samplers"] = [{"magFilter": LINEAR, "minFilter": LINEAR_MIPMAP_LINEAR,
                            "wrapS": CLAMP_TO_EDGE, "wrapT": CLAMP_TO_EDGE}]
        doc["textures"] = [{"sampler": 0, "source": 0}]
    return doc


def encode_glb(local_xyz: np.ndarray, uv: np.ndarray, triangles: np.ndarray,
               jpeg: bytes | None = None) -> bytes:
    q = quantize(z_up_to_y_up(local_xyz))
    positions = np.zeros((len(q.values), 4), np.uint16)
    positions[:, :3] = q.values
    tex = np.round(np.clip(uv, 0, 1) * QUANT_MAX).astype(np.uint16)
    small = len(q.values) < QUANT_MAX
    indices = triangles.astype(np.uint16 if small else np.uint32).ravel()
    blobs = [positions.tobytes(), tex.tobytes(), indices.tobytes()]
    targets: list[int | None] = [ARRAY_BUFFER, ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER]
    strides: list[int | None] = [8, None, None]
    if jpeg is not None:
        blobs.append(jpeg)
        targets.append(None)
        strides.append(None)
    views, binary = _views(blobs, targets, strides)
    doc = gltf_document(q, len(tex), len(indices), UNSIGNED_SHORT if small else UNSIGNED_INT,
                        views, len(binary), jpeg is not None)
    json_chunk = _pad4(json.dumps(doc, separators=(",", ":")).encode(), b" ")
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    return b"".join([
        struct.pack("<III", GLB_MAGIC, 2, total),
        struct.pack("<II", len(json_chunk), JSON_CHUNK), json_chunk,
        struct.pack("<II", len(binary), BIN_CHUNK), binary,
    ])


def read_glb(glb: bytes) -> tuple[dict, bytes]:
    json_len = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20:20 + json_len])
    return doc, glb[20 + json_len + 8:]


def decode_positions(glb: bytes) -> np.ndarray:
    doc, binary = read_glb(glb)
    accessor = doc["accessors"][0]
    view = doc["bufferViews"][accessor["bufferView"]]
    raw = np.frombuffer(binary, np.uint16, accessor["count"] * 4, view["byteOffset"])
    matrix = np.array(doc["nodes"][0]["matrix"]).reshape(4, 4).T
    yup = raw.reshape(-1, 4)[:, :3].astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]
    return y_up_to_z_up(yup)


def obb(local_xyz: np.ndarray, up: np.ndarray) -> list[float]:
    east = np.cross([0.0, 0.0, 1.0], up)
    east = east / np.linalg.norm(east) if np.linalg.norm(east) > 1e-9 else np.array([1.0, 0, 0])
    north = np.cross(up, east)
    axes = np.stack([east, north, up])
    proj = local_xyz @ axes.T
    low, high = proj.min(axis=0), proj.max(axis=0)
    centre = ((low + high) / 2) @ axes
    half = ((high - low) / 2 + 1.0)[:, None] * axes
    return [*centre.tolist(), *half.ravel().tolist()]


def translation(vector: np.ndarray) -> list[float]:
    m = np.eye(4)
    m[:3, 3] = vector
    return m.T.ravel().tolist()


def shift_box(box: list[float], offset: np.ndarray) -> list[float]:
    return [*(np.array(box[:3]) + offset).tolist(), *box[3:]]


def sagitta_m(angle_deg: float) -> float:
    return float(MARS_RADIUS_M * (1 - np.cos(np.radians(angle_deg) / 2)))


def upsample_corners(coarse: np.ndarray) -> np.ndarray:
    n = coarse.shape[0]
    fine = np.empty((2 * n - 1, 2 * n - 1), np.float64)
    c = coarse.astype(np.float64)
    fine[::2, ::2] = c
    fine[1::2, ::2] = (c[:-1] + c[1:]) / 2
    fine[::2, 1::2] = (c[:, :-1] + c[:, 1:]) / 2
    fine[1::2, 1::2] = (c[:-1, :-1] + c[1:, :-1] + c[:-1, 1:] + c[1:, 1:]) / 4
    return fine


def refinement_error(coarse: np.ndarray, fine: np.ndarray) -> float:
    return float(np.max(np.abs(upsample_corners(coarse) - fine)))
