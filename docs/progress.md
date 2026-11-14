# Progress log

Newest session first. Each entry: date, milestone, what changed, evidence (command output), open problems, next step.

## Open decisions
| ID | Question | Status |
|---|---|---|
| D-001 | CRS code written in layer.json (proposed IAU_2015:49900) | open, issue #2, `docs/decisions/D-001-crs-code.md`, rec: keep 49900 as body+datum code |
| D-002 | Terrain tiler for a Mars sphere | open, spike in M1 |
| D-003 | One R2 bucket with prefixes vs two buckets | open, issue #3, `docs/decisions/D-003-r2-buckets.md`, rec: one bucket + write guard in `common/storage.py` |

## Sessions
### 2026-11-14 — M0 Repo bootstrap (status: review, PR #1)

**Environment setup (before M0):**
- Moved harness docs into `docs/`; replaced `<owner>` with `naza-official` in `docs/contracts.md`, `docs/plan-person-a.md`, `docs/milestones.json` (README_HARNESS step 2, placeholder fill, no contract change).
- Created `.venv` (uv, CPython 3.11.14). Conda `base` is 3.10 and fails init; activate `.venv` before `scripts/init.sh`.
- Bootstrapped cloud (human approved): S3 bucket `mars-sv-pulumi-state-123412369922` (us-west-2, versioned, public access blocked, SSE-S3, tag `project=mars-sv`); KMS key `bb0ceb01-c422-4f36-8798-51562af39590` with alias `alias/pulumi` (~$1/month).
- Pulumi login URL: `s3://mars-sv-pulumi-state-123412369922?region=us-west-2&awssdk=v2&profile=claude-agent` (region/profile in URL so plain `pulumi whoami` works).
- `scripts/init.sh` → `init ok` (run by human; agent permission denied for that script).
- Harness committed on `main` as `4f82daf` and pushed (human approved).

**What changed (branch `m0-repo-bootstrap`):**
- Layout: `common/`, `layers/{l1_global,l2_regional,l5_thermal}/`, `viewer/`, `pipelines/Snakefile`, `infra/{storage,build_vm}/`, `configs/sites/jezero.yaml`, `tests/fixtures/`.
- `pyproject.toml` (package `mars-sv-common`, module `common`, ruff, pytest), `environment.yml` (conda-forge, python 3.11 + gdal, rasterio, pyproj, pystac, pystac-client, snakemake, boto3, jsonschema).
- `common/sites.py` site-config loader + `tests/test_sites.py`.
- `configs/sites/jezero.yaml`: bbox `[77.2, 18.2, 77.7, 18.7]`, centre `[77.5, 18.4]` (plan value), local eqc on the 3,396,190 m sphere. No CRS code written (D-001 open).
- `.github/workflows/ci.yml`: pip install on Python 3.11, `ruff check .`, `pytest -q` on push and PR. Uses pip, not conda, because M0 needs no GDAL; switch to micromamba in M1 when `common` needs rasterio.

**Evidence (local):**
```
$ ruff check .
All checks passed!
$ pytest -q
....                                                                     [100%]
4 passed in 0.11s
$ test -f configs/sites/jezero.yaml
exit 0
$ gh run list --limit 1
completed	success	M0: repo bootstrap	ci	m0-repo-bootstrap	pull_request	43602435295	25s	2026-11-14T13:58:41Z
```

**Follow-up in the same session (after PR opened):**
- Fixed `configs/sites/jezero.yaml`: contract says site CRS is "centred on the site", but `lat_0` was 0 (centre projected to y = 1,090 km). Now `lat_0 = lon/lat centre`, `base_crs: IAU_2015:49900`; added a test.
```
$ pytest -q
5 passed in 0.02s
$ python (Transformer IAU_2015:49900 -> jezero proj4)
centre -> [0.0, 0.0]
SW -> [-16873.3, -11854.9] NE -> [11248.9, 17782.4]
```
- Snakefile dry run (snakemake 9.27.1 installed in `.venv` only, not in pyproject):
```
$ snakemake -n -s pipelines/Snakefile
neither input nor output: all
This was a dry-run (flag -n).
```
- PROJ 9.5.1 check for D-001: `IAU_2015:49900` = "Mars (2015) - Sphere / Ocentric", R = 3396190, lat north / lon east. A site CRS made from a PROJ string loses the IAU authority; one built with `ProjectedCRS(geodetic_crs=CRS("IAU_2015:49900"))` keeps it. Use the latter in `common/crs.py` (M1).
- Wrote `docs/decisions/D-001-crs-code.md`, `D-003-r2-buckets.md`; created labels `decision`, `contract`; opened issues #2 (D-001) and #3 (D-003).

**Open problems:**
- `.claude/hooks/guard.sh` and `.claude/commands/*` from README_HARNESS are missing in the repo.

**Next step:** human reviews PR https://github.com/naza-official/mars-street-view/pull/1, merges, sets M0 `done`. Then M1 (L0 common library + terrain-tiler spike).
