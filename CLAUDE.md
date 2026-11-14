# Mars Street View — agent guide (Person A lane)

You are building the **orbital half and the viewer** of a Mars "street view": a whole-planet globe, sharper terrain around rover sites, and a web viewer that later shows Person B's rover panoramas. Person B builds rover poses and panoramas in parallel. The two halves meet only through files in a shared R2 bucket described by `layer.json`.

## Read before any work
1. `docs/progress.md` — what previous sessions did and what is next.
2. `docs/milestones.json` — the milestone list, status, and acceptance checks. This is the source of truth for scope.
3. `docs/contracts.md` — CRS, datum, formats, bucket layout, `layer.json`. Never deviate from it.
4. `docs/plan-person-a.md` — full context and per-milestone task detail. Read only the section for the current milestone.

## Session loop
1. Run `scripts/init.sh`. If it fails, fix the environment or report; do not start feature work.
2. Pick the first milestone in `docs/milestones.json` whose status is `todo` and whose `depends_on` are all `done`. Work on that one milestone only.
3. Create branch `m<N>-<slug>`. Commit small, working steps with clear messages.
4. Before claiming done, run every command in the milestone's `acceptance` list and paste the results into `docs/progress.md`.
5. Set the milestone status to `review` (never `done`; only the human sets `done`). Open a PR with `gh pr create`.
6. Append a session entry to `docs/progress.md`: what changed, evidence, open problems, next step.

## Hard rules
- **Cloud changes:** `pulumi preview` freely. `pulumi up`, `pulumi destroy`, and anything that starts compute need human approval; Claude Code will ask. Never pass `--yes` or `--skip-preview`.
- **Always tear down:** after a cloud job, confirm the instance terminated and its volumes are gone. Log the run in `docs/runs.md` (date, milestone, instance type, hours, estimated USD).
- **AWS scope:** profile `claude-agent`, account `123412369922`, region `us-west-2` only. IAM roles you create must be named `msv-*` and carry the permissions boundary `arn:aws:iam::123412369922:policy/agent-boundary`.
- **Data:** never mirror whole archives. Fetch by bounding box, stream COGs from the USGS S3 buckets in `us-west-2`. Raw data lives on the build VM or in `raw/`, never in Git.
- **Publishing:** outputs go to `orbital/<layer>/<site>/vN/`. Published versions are immutable; a fix is `vN+1`. Upload `layer.json` last, only after `python -m common.validate` passes.
- **Contracts:** changes to `common/`, `docs/contracts.md`, or the `layer.json` schema need Person B's review. Open an issue labeled `contract` first.
- **Unknowns:** if a decision is missing, open a GitHub issue labeled `decision`, write the options and your recommendation in `docs/decisions/`, and move to another task. Do not guess on contracts.
- **Secrets:** never read or print `~/.aws/*`, `.env*`, or Pulumi secret values. R2 keys come from SSM Parameter Store at runtime.
- **Code style:** Python 3.11, ruff, type hints, no code comments, tests with pytest. Keep functions small.

## Commands
- `scripts/init.sh` — environment and credential check
- `pytest -q` — unit tests
- `python -m common.validate <path-or-r2-uri>` — validate a layer output
- `snakemake -n <target>` — dry run a pipeline target
- `cd infra/<stack> && pulumi preview` — infra diff

## When stuck
If the same approach has failed twice, stop, write what you tried in `docs/progress.md`, and ask the human. Do not loop.
