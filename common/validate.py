import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.warp import transform_bounds

from common import layerjson, storage
from common.cog import BLOCK_SIZE, is_cog_layout
from common.crs import (
    BASE_CRS_CODE,
    METRES_PER_DEGREE,
    base_authority,
    geographic,
    is_mars_sphere,
)

ALLOWED_DTYPES = {"float32", "uint8"}
HEIGHT_RANGE_M = (-30000.0, 30000.0)
KIND_TAG = "MSV_KIND"
UNITS_TAG = "UNITS"
RASTER_SUFFIXES = (".tif", ".tiff")


@dataclass
class Report:
    target: str
    failures: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    lonlat: list[float] | None = None

    def check(self, ok: bool, message: str) -> bool:
        if not ok:
            self.failures.append(message)
        return ok

    def merge(self, other: "Report", prefix: str) -> None:
        self.failures += [f"{prefix}: {m}" for m in other.failures]
        self.info += [f"{prefix}: {m}" for m in other.info]

    @property
    def ok(self) -> bool:
        return not self.failures


def lonlat_bounds(src: rasterio.DatasetReader) -> tuple[float, float, float, float]:
    return transform_bounds(src.crs, rasterio.crs.CRS.from_wkt(geographic().to_wkt()),
                            *src.bounds, densify_pts=21)


def _check_crs(src: rasterio.DatasetReader, report: Report) -> bool:
    if not report.check(src.crs is not None, "no CRS"):
        return False
    crs = CRS.from_wkt(src.crs.to_wkt())
    sphere = report.check(is_mars_sphere(crs), f"CRS is not the Mars sphere: {crs.name}")
    authority = base_authority(crs)
    report.check(authority == BASE_CRS_CODE,
                 f"geodetic CRS authority is {authority}, expected {BASE_CRS_CODE}")
    report.info.append(f"crs: {crs.name}")
    return sphere


def _check_structure(src: rasterio.DatasetReader, report: Report) -> None:
    report.check(src.nodata is not None, "nodata not set")
    report.check(set(src.dtypes) <= ALLOWED_DTYPES, f"dtype {src.dtypes[0]} not float32/uint8")
    report.check(is_cog_layout(src), "not COG layout (IMAGE_STRUCTURE LAYOUT != COG)")
    block_h, block_w = src.block_shapes[0]
    report.check(block_w < src.width or src.width <= BLOCK_SIZE, "not internally tiled")
    report.check(block_h > 1, "strip layout, not tiles")
    needs_overviews = max(src.width, src.height) > BLOCK_SIZE
    report.check(not needs_overviews or bool(src.overviews(1)), "no overviews")
    compression = src.compression.value if src.compression else "none"
    report.info.append(f"size: {src.width}x{src.height}x{src.count} {src.dtypes[0]} "
                       f"block {block_w}x{block_h} overviews {src.overviews(1)} {compression}")


def _check_bounds(src: rasterio.DatasetReader, report: Report) -> None:
    west, south, east, north = report.lonlat = list(lonlat_bounds(src))
    finite = all(math.isfinite(v) for v in (west, south, east, north))
    report.check(finite and -90 <= south < north <= 90, f"bad lon/lat bounds {south}..{north}")
    report.info.append(f"bounds_lonlat: [{west:.4f}, {south:.4f}, {east:.4f}, {north:.4f}]")
    report.info.append(f"resolution: {abs(src.res[0]):.3f} x {abs(src.res[1]):.3f}")


def _check_values(src: rasterio.DatasetReader, report: Report) -> None:
    tags = src.tags()
    kind = tags.get(KIND_TAG)
    report.info.append(f"kind: {kind or 'untagged'}")
    if kind == "thermal":
        report.check(bool(tags.get(UNITS_TAG)), "thermal raster has no UNITS tag")
    if kind != "elevation":
        return
    data = src.read(1, masked=True, out_shape=(min(src.height, 1024), min(src.width, 1024)))
    report.check(data.count() > 0, "elevation raster has no valid pixels")
    if data.count() == 0:
        return
    low, high = float(data.min()), float(data.max())
    report.info.append(f"height range: {low:.1f} .. {high:.1f} m")
    report.check(HEIGHT_RANGE_M[0] <= low and high <= HEIGHT_RANGE_M[1],
                 f"heights {low:.0f}..{high:.0f} m outside {HEIGHT_RANGE_M}")


def validate_raster(path: str, env: dict[str, str] | None = None) -> Report:
    report = Report(path)
    with rasterio.Env(**(env or {})), rasterio.open(path) as src:
        if _check_crs(src, report):
            _check_bounds(src, report)
        _check_structure(src, report)
        _check_values(src, report)
    return report


def validate_doc(doc: dict, target: str) -> Report:
    report = Report(target)
    for problem in layerjson.errors(doc):
        report.check(False, problem)
    report.info.append(layerjson.schema_for(doc)["title"])
    return report


def _lon360(value: float) -> float:
    return value % 360.0


def bounds_inside(inner: list[float], outer: list[float], tolerance_deg: float) -> bool:
    iw, i_s, ie, i_n = inner
    ow, o_s, oe, o_n = outer
    if (oe - ow) % 360 == 0 and oe != ow:
        return i_s >= o_s - tolerance_deg and i_n <= o_n + tolerance_deg
    return (
        _lon360(iw) >= _lon360(ow) - tolerance_deg
        and _lon360(ie) <= _lon360(oe) + tolerance_deg
        and i_s >= o_s - tolerance_deg
        and i_n <= o_n + tolerance_deg
    )


class Target:
    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.remote = storage.is_remote(raw)
        self.uri = storage.parse_uri(raw) if self.remote else None

    def child(self, name: str) -> "Target":
        if self.uri is not None:
            return Target(f"{self.uri.scheme}://{self.uri.bucket}/{self.uri.child(name).key}")
        return Target(str(Path(self.raw) / name))

    def exists(self) -> bool:
        if self.uri is None:
            return Path(self.raw).exists()
        return self.uri.key in storage.list_keys(storage.ObjectUri(
            self.uri.scheme, self.uri.bucket, self.uri.key.rsplit("/", 1)[0]))

    def read_json(self) -> dict:
        if self.uri is None:
            return json.loads(Path(self.raw).read_text())
        return json.loads(storage.read_bytes(self.uri))

    def raster_path(self) -> str:
        return self.uri.gdal_path if self.uri is not None else self.raw

    def gdal_env(self) -> dict[str, str]:
        return storage.gdal_env(self.uri.scheme) if self.uri is not None else {}

    def is_raster(self) -> bool:
        return self.raw.lower().endswith(RASTER_SUFFIXES)

    def is_json(self) -> bool:
        return self.raw.lower().endswith(".json")


def validate_layer(root: Target) -> Report:
    report = Report(root.raw)
    manifest = root.child("layer.json")
    if not report.check(manifest.exists(), "layer.json missing"):
        return report
    doc = manifest.read_json()
    report.merge(validate_doc(doc, manifest.raw), "layer.json")
    for name, relative in doc.get("assets", {}).items():
        report.merge(_validate_asset(root.child(relative), doc), f"asset {name}")
    return report


def _validate_asset(asset: Target, doc: dict) -> Report:
    if not asset.exists():
        report = Report(asset.raw)
        report.check(False, "missing")
        return report
    if not asset.is_raster():
        return Report(asset.raw, info=["present"])
    report = validate_raster(asset.raster_path(), asset.gdal_env())
    lonlat = report.lonlat
    resolution_deg = 2.0 * float(doc.get("resolution_m", 0)) / METRES_PER_DEGREE
    if lonlat is not None and "bounds_lonlat" in doc:
        report.check(bounds_inside(lonlat, doc["bounds_lonlat"], max(resolution_deg, 1e-6)),
                     f"raster bounds {np.round(lonlat, 4).tolist()} outside layer bounds")
    return report


def validate(raw: str) -> Report:
    target = Target(raw)
    if target.is_raster():
        return validate_raster(target.raster_path(), target.gdal_env())
    if target.is_json():
        return validate_doc(target.read_json(), raw)
    return validate_layer(target)


def print_report(report: Report) -> None:
    for line in report.info:
        print(f"  {line}")
    for line in report.failures:
        print(f"FAIL {line}")
    print(f"{'ok' if report.ok else 'invalid'} {report.target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m common.validate",
        description="Validate a COG, a layer.json / catalog index, or a layer folder "
                    "(local path, s3:// or r2:// URI).",
    )
    parser.add_argument("target", help="path or URI")
    args = parser.parse_args(argv)
    report = validate(args.target)
    print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
