"""Minimal PDS4 reader for MIPL rover products (image arrays, Cylindrical lander projection).

Only what L4 needs: read the first image array as numpy with scaling and missing values applied,
and pull a few cartography/geometry fields from the label. Namespaces are ignored on purpose.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_DTYPES = {
    "SignedMSB2": ">i2",
    "UnsignedMSB2": ">u2",
    "SignedLSB2": "<i2",
    "UnsignedLSB2": "<u2",
    "SignedMSB4": ">i4",
    "UnsignedMSB4": ">u4",
    "SignedLSB4": "<i4",
    "UnsignedLSB4": "<u4",
    "IEEE754MSBSingle": ">f4",
    "IEEE754LSBSingle": "<f4",
    "IEEE754MSBDouble": ">f8",
    "IEEE754LSBDouble": "<f8",
    "UnsignedByte": "u1",
    "SignedByte": "i1",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, name):
    for e in el.iter():
        if _local(e.tag) == name:
            return e
    return None


def _text(el, name, cast=str, default=None):
    e = _find(el, name)
    return cast(e.text) if e is not None and e.text is not None else default


@dataclass
class Cylindrical:
    pixel_scale_deg: float  # pixels per degree (x == y for MIPL cyl)
    max_elevation: float
    min_elevation: float
    start_azimuth: float
    stop_azimuth: float
    zero_elevation_line: float  # 0-based line where elevation = 0 (MIPL convention, see SIS)
    origin_site_ned_m: tuple[
        float, float, float
    ]  # projection origin in the SITE frame (x N, y E, z down)


def read_label(xml_path: Path) -> ET.Element:
    return ET.parse(xml_path).getroot()


def image_file(root: ET.Element, xml_path: Path) -> Path:
    for fa in root.iter():
        if _local(fa.tag) == "File_Area_Observational":
            return xml_path.with_name(_text(_find(fa, "File"), "file_name"))
    raise ValueError("no File_Area_Observational")


def read_array(xml_path: Path) -> np.ndarray:
    """Return the image as float32 (bands, lines, samples) with missing/invalid as NaN."""
    root = read_label(xml_path)
    arr = None
    for e in root.iter():
        if _local(e.tag) in ("Array_3D_Image", "Array_2D_Image", "Array_3D", "Array_2D"):
            arr = e
            break
    if arr is None:
        raise ValueError("no image array in label")
    offset = int(_text(arr, "offset"))
    ee = _find(arr, "Element_Array")
    dtype = np.dtype(_DTYPES[_text(ee, "data_type")])
    scale = _text(ee, "scaling_factor", float, 1.0)
    voff = _text(ee, "value_offset", float, 0.0)
    axes = sorted(
        [
            (int(_text(ax, "sequence_number")), _text(ax, "axis_name"), int(_text(ax, "elements")))
            for ax in arr
            if _local(ax.tag) == "Axis_Array"
        ]
    )
    shape = [n for _, _, n in axes]
    names = [nm for _, nm, _ in axes]
    raw = np.fromfile(
        image_file(root, xml_path), dtype=dtype, count=int(np.prod(shape)), offset=offset
    )
    raw = raw.reshape(shape)
    if names[0] != "Band":
        raw = raw[None] if len(shape) == 2 else np.moveaxis(raw, names.index("Band"), 0)
    sc = _find(arr, "Special_Constants")
    bad = np.zeros(raw.shape, bool)
    if sc is not None:
        for name in ("missing_constant", "invalid_constant", "null_constant"):
            v = _text(sc, name, float)
            if v is not None:
                bad |= raw == v
    out = raw.astype(np.float32) * scale + voff
    out[bad] = np.nan
    return out


def cylindrical(xml_path: Path) -> Cylindrical:
    root = read_label(xml_path)
    c = _find(root, "Cylindrical")
    if c is None:
        raise ValueError("not a Cylindrical lander projection")
    o = _find(c, "Vector_Projection_Origin")
    return Cylindrical(
        pixel_scale_deg=_text(c, "pixel_scale_x", float),
        max_elevation=_text(c, "maximum_elevation", float),
        min_elevation=_text(c, "minimum_elevation", float),
        start_azimuth=_text(c, "start_azimuth", float),
        stop_azimuth=_text(c, "stop_azimuth", float),
        zero_elevation_line=_text(c, "zero_elevation_line", float),
        origin_site_ned_m=tuple(
            _text(o, k, float) for k in ("x_position", "y_position", "z_position")
        ),
    )
