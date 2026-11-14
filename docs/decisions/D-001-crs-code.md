# D-001 CRS code written in layer.json

## Context
`docs/contracts.md` proposes `"crs": "IAU_2015:49900"` in every `layer.json`. Layers use different 2D CRSs on the same body:
global simple cylindrical 0 to 360 E, a local equirectangular per site, and Mars-fixed XYZ for meshes. One string field cannot name all of them exactly.

Checked locally on 2026-11-14 with pyproj 3.x / PROJ 9.5.1:
```
IAU_2015:49900 | Mars (2015) - Sphere / Ocentric | Geographic 2D CRS | R= 3396190.0 3396190.0
IAU_2015:49910 | Mars (2015) - Sphere / Ocentric / Equirectangular, clon = 0 | Projected CRS | R= 3396190.0 3396190.0
IAU_2015:49901 | Mars (2015) / Ographic | Geographic 2D CRS | R= 3396190.0 3376200.0
49900 axes: latitude north, longitude east (degrees)
```
- 49900 is exactly the contract sphere (radius 3,396,190 m, east-positive).
- A site CRS built from a PROJ string (`+proj=eqc ... +R=3396190`) loses the authority: `geodetic_crs.to_authority()` returns `None`.
- A site CRS built in pyproj as `ProjectedCRS(..., geodetic_crs=CRS("IAU_2015:49900"))` keeps it: `('IAU_2015', '49900')`.
- There is no authority code for a site-centred equirectangular; 49910 is centred on longitude 0.

## Options
1. **`crs` = base geographic CRS code, always `IAU_2015:49900`.** It names body + datum only. The exact 2D CRS lives in each COG's GeoTIFF keys, and the mesh frame is fixed by contract (Mars-fixed XYZ).
   Simple, one value for both lanes. The validator can check that each raster's geodetic CRS matches 49900.
2. **`crs` = the layer's actual CRS**: an IAU code when one exists (49900, 49910), otherwise WKT2 or PROJJSON inline.
   Exact, but the field becomes a mixed type and is harder to compare. Schema change.
3. **Two fields: `crs` (base code, option 1) + `crs_2d` (WKT2 or PROJJSON of the layer's raster CRS).**
   Exact and still comparable. Needs schema 1.1 and Person B's review.

## Recommendation
Option 1 now. `layer.json` keeps `"crs": "IAU_2015:49900"` meaning "body and datum", and `common.crs` builds every projected CRS on top of `CRS("IAU_2015:49900")` so GeoTIFFs carry a datum PROJ can identify. Move to option 3 only if a consumer needs the 2D CRS without opening a raster.
Before closing, confirm the GDAL in the M2 image resolves `IAU_2015:49900` (GDAL ≥ 3.5 with PROJ ≥ 8.2).

Update 2026-11-14 (M1): the GDAL 3.10.3 bundled in the rasterio 1.4.4 wheel resolves `IAU_2015:49900` and `49915`.
GeoTIFF keys only carry EPSG codes, so after a write/read round trip a COG's CRS has no authority attached.
PROJ identifies it again: the projected CRS matches `IAU_2015:49915` at confidence ≥ 90, and the geodetic part equals
49900 up to axis order (49900 is lat/lon, GeoTIFF is lon/lat). `common.crs.base_authority` compares with
`ignore_axis_order=True`, so `common.validate` can enforce option 1 on every raster. The global CRS uses
`IAU_2015:49915` (sphere equirectangular, clon = 180), which matches the contract's "0 to 360 E".

## Decision
_(human)_

## Date
2026-11-14 (opened)
