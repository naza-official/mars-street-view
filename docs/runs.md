# Cloud run log

| Date | Milestone | Target | Instance | Hours | Spot price USD/h | Est. USD | Torn down |
|---|---|---|---|---|---|---|---|
| 2026-11-14 | M3 | smoke (run `smoke-global-20261114t175902z`, image v0.3.0; failed: `No module named 'pipelines'`) | m7i.xlarge Spot, us-west-2d, gp3 50 GB | 0.04 | 0.0833 | < 0.01 | yes: terminated, 0 volumes, stack 0 resources |
| 2026-11-14 | M3 | smoke (run `smoke-global-20261114t181551z`, image v0.3.1; status 0, published `orbital/_smoke/global/v1/`) | m7i.xlarge Spot, us-west-2d, gp3 50 GB | 0.04 | 0.0833 | < 0.01 | yes: terminated, 0 volumes, stack 0 resources |
| 2026-11-14 | M4 | l1 (run `l1-global-20261114t200732z`, image v0.4.0; **failed** in `l1_tiles` assemble: `TileKey` not orderable. Ingest, dem_geoid (vs PDS max 1.06 m) and all 43,690 tiles (3.5 GB) ran; no publish) | m7i.2xlarge Spot, us-west-2d, gp3 100 GB | 0.67 | 0.1674 | ~0.33 (0.11 EC2 + ~0.22 for 43,694 PUTs to work/) | instance terminated, 0 volumes; stack destroy pending human confirm |
