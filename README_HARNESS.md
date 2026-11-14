# Harness setup

Copy these files into the root of `mars-street-view/`:

```
CLAUDE.md
KICKOFF_PROMPT.md
.claude/settings.json
.claude/hooks/guard.sh
.claude/commands/next-milestone.md
.claude/commands/review-pr.md
.claude/commands/teardown-check.md
docs/plan-person-a.md
docs/contracts.md
docs/milestones.json
docs/progress.md
docs/runs.md
docs/decisions/README.md
scripts/init.sh
```

Then:
1. `chmod +x scripts/init.sh .claude/hooks/guard.sh`
2. Replace `<owner>` in `docs/milestones.json` and `docs/plan-person-a.md` with your GitHub user or org.
3. `aws sso login --profile claude-agent` and `pulumi login s3://mars-sv-pulumi-state-123412369922`
4. `scripts/init.sh` should print `init ok`.
5. Commit the harness on `main`, then start Claude Code and paste `KICKOFF_PROMPT.md`, or run `/next-milestone`.

Each later session: `aws sso login --profile claude-agent`, start Claude Code, run `/next-milestone`. After a PR, run `/review-pr` in a fresh session. After cloud runs, `/teardown-check`. You set `done` in `docs/milestones.json` after merging.
