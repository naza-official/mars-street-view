# D-008: What "MDIM shift at Jezero under 232 m" measures

**Status:** decided (2026-11-14). **Needed by:** M4 acceptance.

## Context
M4 acceptance says "measured MDIM shift at Jezero under 232 m, recorded in layer.json notes". The plan's risk note is about
the datum: "MDIM's label uses an ellipsoid; convert once at ingest and check Jezero does not shift by more than one 232 m
pixel."

What the sources say (`s3://asc-pds-services/mosaic/*_pds3.lbl`, the targets of the planetarymaps.usgs.gov redirects):
- MDIM 2.1 colour: `A_AXIS_RADIUS = 3396.19`, `C_AXIS_RADIUS = 3376.2` (ellipsoid), but
  `PROJECTION_LATITUDE_TYPE = PLANETOCENTRIC`, `SIMPLE_CYLINDRICAL`, 256 px/deg, `LINE/SAMPLE_PROJECTION_OFFSET` =
  23039.5 / 46079.5 (pixel edges exactly on ±90°, ±180°). The GeoTIFF header already says sphere 3,396,190.
- MOLA 463 m: sphere 3396.19, planetocentric, 128 px/deg, offsets 11519.5 / 23039.5 (same edge convention). Its
  GeoTIFF header origin is off by about half a pixel in x and a quarter pixel in y; we use the PDS grid (as the M1
  fixtures already do).

So moving MDIM to the sphere is a pure longitude roll (−180..180 → 0..360) on the same grid: the **datum-conversion shift
is 0 m**. Reading the latitude as planetographic would have shifted Jezero by about 12 km north; that is the risk the
plan named, and it does not apply.

Measured image registration of MDIM 2.1 against MOLA (`layers/l1_global/shift.py`: normalised cross-correlation of MDIM
brightness with MOLA hillshades over 8 sun azimuths, 384 px window at 463 m, ±24 px search, parabolic sub-pixel peak;
bias test with two hillshades of the same DEM: ≤ 0.01 px):

| Site | East m | North m | Score |
|---|---|---|---|
| Jezero 77.5E 18.4N | −898 | +72 | 0.52 |
| Jezero, 4 more windows ±0.5° | −861 .. −917 | −162 .. +5 | 0.48–0.56 |
| Gale 137.4E 5.4S | −1098 | +233 | 0.59 |
| Syrtis 67E 9N | −907 | −406 | 0.50 |
| Hellas rim 70E 40S | −633 | +914 | 0.68 |
| Elysium 147E 25N | −137 | −59 | 0.51 |
| 30E 55N | +235 | +389 | 0.64 |

The offset varies by region, so it is MDIM 2.1's own registration error against MOLA (Archinal et al. 2003 report errors
of a few hundred metres on average, up to kilometres locally), not a grid-convention bug here. At Jezero the texture sits
about 2 MOLA pixels (4 MDIM pixels) west of the relief.

## Options
1. **Read the criterion as the datum-conversion shift.** Record both numbers in `layer.json` notes: conversion shift 0 m
   (passes), MDIM–MOLA registration 0.9 km at Jezero (known source limitation). L2 (HiRISE, M6) replaces L1 at Jezero.
   No extra work.
2. **Co-register MDIM to MOLA globally in M4.** Measure the offset on a grid of windows (for example every 2°, about
   16,000 windows), drop low-score windows, smooth and interpolate, and warp MDIM with that displacement field. Then the
   measured Jezero offset should be under 232 m. Cost: about a day more work plus a longer build. Risk: weak correlation
   over flat plains, dust and polar caps needs careful filtering.
3. Shift MDIM only around Jezero. It creates a seam; not recommended.

## Recommendation
Option 1 for `l1_global/global/v1`, with option 2 as a later `v2` if the misregistration is visible in the viewer
outside L2 sites.

## Decision
Option 1, decided by the human 2026-11-14: the criterion is the datum-conversion shift. Record both numbers in `layer.json` notes; global co-registration is a possible `l1_global` v2.

## Date
2026-11-14 (opened)
