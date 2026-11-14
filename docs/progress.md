# Progress log

Newest session first. Each entry: date, milestone, what changed, evidence (command output), open problems, next step.

## Open decisions
| ID | Question | Status |
|---|---|---|
| D-001 | CRS code written in layer.json (proposed IAU_2015:49900) | open |
| D-002 | Terrain tiler for a Mars sphere | open, spike in M1 |
| D-003 | One R2 bucket with prefixes vs two buckets | open, default one bucket |

## Sessions
### 2026-11-14 — M0 Repo bootstrap (status: in_progress, waiting on push + CI)

**Environment setup (before M0):**
- Moved harness docs into `docs/`; replaced `<owner>` with `naza-official` in `docs/contracts.md`, `docs/plan-person-a.md`, `docs/milestones.json` (README_HARNESS step 2, placeholder fill, no contract change).
- Created `.venv` (uv, CPython 3.11.14). Conda `base` is 3.10 and fails init; activate `.venv` before `scripts/init.sh`.
- Bootstrapped cloud (human approved): S3 bucket `mars-sv-pulumi-state-123412369922` (us-west-2, versioned, public access blocked, SSE-S3, tag `project=mars-sv`); KMS key `bb0ceb01-c422-4f36-8798-51562af39590` with alias `alias/pulumi` (~$1/month).
- Pulumi login URL: `s3://mars-sv-pulumi-state-123412369922?region=us-west-2&awssdk=v2&profile=claude-agent` (region/profile in URL so plain `pulumi whoami` works).
- `scripts/init.sh` → `init ok` (run by human; agent permission denied for that script).
- Harness committed locally on `main` as `4f82daf`; **not pushed yet**.

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
(pending: needs push of main + branch and a PR)
```

**Open problems:**
- Push of `main` was blocked by the agent permission classifier; human must push or approve.
- `pipelines/Snakefile` not dry-run checked locally (snakemake not in `.venv`).
- `.claude/hooks/guard.sh` and `.claude/commands/*` from README_HARNESS are missing in the repo.

**Next step:** push `main` and `m0-repo-bootstrap`, open PR, paste `gh run list --limit 1` output here, set M0 to `review`.
