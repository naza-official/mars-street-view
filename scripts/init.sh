#!/usr/bin/env bash
set -euo pipefail
fail=0
check() { if eval "$2" >/dev/null 2>&1; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
check "git repo" "git rev-parse --is-inside-work-tree"
check "python 3.11+" "python -c 'import sys; assert sys.version_info >= (3,11)'"
check "aws cli" "command -v aws"
check "pulumi cli" "command -v pulumi"
check "gh cli" "gh auth status"
check "docker" "docker info"
check "aws session (claude-agent)" "aws sts get-caller-identity --profile claude-agent"
acct=$(aws sts get-caller-identity --profile claude-agent --query Account --output text 2>/dev/null || true)
if [ "$acct" != "123412369922" ]; then echo "FAIL wrong or missing account: '$acct'"; fail=1; fi
check "pulumi backend" "pulumi whoami"
running=$(aws ec2 describe-instances --profile claude-agent --region us-west-2 --filters Name=tag:project,Values=mars-sv Name=instance-state-name,Values=pending,running --query 'length(Reservations)' --output text 2>/dev/null || echo "?")
echo "info running mars-sv instances: $running"
git status --short | head -20
if [ "$fail" -ne 0 ]; then echo "init failed: run 'aws sso login --profile claude-agent' or fix tools above"; exit 1; fi
echo "init ok"
