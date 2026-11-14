import json
import struct

import numpy as np

FLOAT, UINT16, UINT32 = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


def z_up_to_y_up(local: np.ndarray) -> np.ndarray:
    return np.stack([local[:, 0], local[:, 2], -local[:, 1]], axis=1)


def y_up_to_z_up(gltf: np.ndarray) -> np.ndarray:
    return np.stack([gltf[:, 0], -gltf[:, 2], gltf[:, 1]], axis=1)


def _pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def encode_glb(positions_local: np.ndarray, uv: np.ndarray, indices: np.ndarray) -> bytes:
    pos = z_up_to_y_up(positions_local).astype(np.float32)
    tex = uv.astype(np.float32)
    idx_type = UINT16 if positions_local.shape[0] < 65536 else UINT32
    idx = indices.astype(np.uint16 if idx_type == UINT16 else np.uint32).ravel()
    blobs = [_pad(pos.tobytes(), b"\0"), _pad(tex.tobytes(), b"\0"), _pad(idx.tobytes(), b"\0")]
    offsets = np.cumsum([0] + [len(b) for b in blobs[:-1]]).tolist()
    gltf = {
        "asset": {"version": "2.0", "generator": "mars-sv spike grid mesher"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                                    "indices": 2, "material": 0, "mode": 4}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.75, 0.45, 0.3, 1],
                                                "metallicFactor": 0, "roughnessFactor": 1}}],
        "buffers": [{"byteLength": sum(len(b) for b in blobs)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": offsets[0], "byteLength": pos.nbytes,
             "target": ARRAY_BUFFER},
            {"buffer": 0, "byteOffset": offsets[1], "byteLength": tex.nbytes,
             "target": ARRAY_BUFFER},
            {"buffer": 0, "byteOffset": offsets[2], "byteLength": idx.nbytes,
             "target": ELEMENT_ARRAY_BUFFER},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": FLOAT, "count": len(pos), "type": "VEC3",
             "min": pos.min(axis=0).tolist(), "max": pos.max(axis=0).tolist()},
            {"bufferView": 1, "componentType": FLOAT, "count": len(tex), "type": "VEC2"},
            {"bufferView": 2, "componentType": idx_type, "count": len(idx), "type": "SCALAR"},
        ],
    }
    json_chunk = _pad(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    bin_chunk = b"".join(blobs)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return b"".join([
        struct.pack("<III", 0x46546C67, 2, total),
        struct.pack("<II", len(json_chunk), 0x4E4F534A), json_chunk,
        struct.pack("<II", len(bin_chunk), 0x004E4942), bin_chunk,
    ])


def decode_positions(glb: bytes) -> np.ndarray:
    json_len = struct.unpack_from("<I", glb, 12)[0]
    gltf = json.loads(glb[20:20 + json_len])
    bin_start = 20 + json_len + 8
    view = gltf["bufferViews"][gltf["accessors"][0]["bufferView"]]
    count = gltf["accessors"][0]["count"]
    raw = np.frombuffer(glb, np.float32, count * 3, bin_start + view["byteOffset"])
    return y_up_to_z_up(raw.reshape(-1, 3).astype(np.float64))
