# Contracts (shared by Person A and Person B)

Changing anything here requires an issue labeled `contract`, a schema version bump, and review by both people.

## Geodesy
| Item | Decision |
|---|---|
| Body shape | IAU sphere, radius 3,396,190 m |
| Heights | Height above that sphere ("delta radius"). Areoid heights are converted at ingest with ASP `dem_geoid`. |
| 2D CRS, global | Simple cylindrical, longitude 0 to 360 E, on the sphere |
| 2D CRS, site | Local equirectangular centred on the site, on the sphere |
| 3D frame | Mars-fixed body-centred XYZ, metres |
| CRS code in metadata | Proposed `IAU_2015:49900`; open decision D-001 |

## Formats
| Data | Normalised | Served |
|---|---|---|
| Elevation | COG, float32, metres above sphere, nodata set | Terrain meshes in 3D Tiles 1.1 (GLB) |
| Orbital imagery | COG, uint8, one per source | Textures on terrain tiles (JPEG or KTX2) |
| Thermal rasters | COG, float32, units in metadata | COG overlay tiles + colour ramp |
| Rover poses (B) | GeoParquet, one row per site + drive | Stop graph GeoJSON |
| Rover terrain (B) | glTF in stop-local ENU + 4x4 transform to Mars-fixed | 3D Tiles child tilesets |
| Metadata | One STAC Item per asset | Static STAC catalog + `layer.json` |

Meshes store small local coordinates plus a transform to Mars-fixed XYZ to avoid float32 jitter.

## Bucket layout (AWS S3, bucket `mars-sv-123412369922`, region `us-west-2`)
```
orbital/<layer>/<site|global>/vN/     A writes
rover/<layer>/<site>/vN/              B writes
catalog/index.json                    both update; current version per layer
catalog/stac/                         STAC items
work/<run_id>/                        intermediate step outputs, deletable
```
- Published `vN/` folders are immutable.
- A version is ready only when its `layer.json` exists; upload it last.
- Lane write access is enforced by IAM: A's role writes `orbital/*`, B's role writes `rover/*`, both write `catalog/*` and `work/*`.

## layer.json (schema 1.0)
```json
{
  "layer": "l2_regional",
  "site": "jezero",
  "version": "v1",
  "schema": "1.0",
  "owner": "A",
  "crs": "IAU_2015:49900",
  "datum": "sphere_3396190",
  "bounds_lonlat": [77.2, 18.2, 77.7, 18.7],
  "resolution_m": 1.0,
  "assets": {"dtm": "dtm.tif", "ortho": "ortho.tif", "tileset": "tileset.json"},
  "depends_on": ["orbital/l1_global/global/v1"],
  "sources": [{"url": "https://...", "fetched": "2026-11-20", "license": "public domain"}],
  "validated": true,
  "built_with": "ghcr.io/naza-official/mars-sv:0.3.1",
  "notes": ""
}
```

## catalog/index.json
```json
{"schema": "1.0", "layers": {"l1_global/global": "v1", "l2_regional/jezero": "v1", "l3_poses/jezero": "v1"}}
```

## Stop record (written by B, read by A's viewer)
```json
{
  "stop_id": "m2020_site<site>_drive<drive>",
  "rover": "perseverance",
  "site": 0, "drive": 0, "sol": 0,
  "position_xyz_m": [0, 0, 0],
  "lon_e": 0, "lat_n": 0, "height_above_sphere_m": 0,
  "attitude_quat": [0, 0, 0, 1],
  "pose_source": "PLACES",
  "alignment_rms_m": 0,
  "panorama": "rover/l4_streetview/jezero/v1/<stop_id>/pano/",
  "mesh_tileset": "rover/l4_streetview/jezero/v1/<stop_id>/tileset.json",
  "next_stops": []
}
```
The viewer hides stops with `alignment_rms_m` above a configurable threshold (default 1.0 m).

## Handoffs
| Direction | What | Where |
|---|---|---|
| A to B | Jezero DTM + ortho COGs on the sphere datum | `orbital/l2_regional/jezero/v1/dtm.tif`, `ortho.tif` |
| B to A | Stop graph, stop records, per-stop tilesets | `rover/l3_poses/jezero/vN/`, `rover/l4_streetview/jezero/vN/` |
| Both | `common/` package version and image tag | `pyproject.toml`, `ghcr.io/naza-official/mars-sv` |

## Changelog
- 2026-11-14: storage moved from Cloudflare R2 (`mars-sv`) to AWS S3 (`mars-sv-123412369922`, us-west-2), decided by the human (low traffic, one cloud). Resolves D-003. Schema version unchanged: `layer.json` holds relative asset paths only.
