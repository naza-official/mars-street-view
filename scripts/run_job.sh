#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'TXT'
usage: scripts/run_job.sh <target> <site>
  Runs one pipeline target on a Spot build VM (infra/build_vm, stack "job"):
  pulumi up (you confirm) -> poll s3://.../work/<run_id>/DONE -> pulumi destroy (you confirm)
  -> check that no mars-sv instances or volumes are left.
  Needs a terminal: pulumi asks for confirmation and this script never passes --yes.
env:
  MSV_INSTANCE_TYPES  comma list, priority order (default: stack config / m7i.4xlarge,...)
  MSV_VOLUME_GB       root gp3 size (default 500)
  MSV_IMAGE_TAG       image tag (default: stack config / job.py)
  MSV_TIMEOUT_MIN     give up polling after this many minutes (default 240)
  MSV_POLL_SEC        poll interval (default 60)
TXT
}

[[ $# -eq 2 ]] || { usage; exit 2; }
[[ -t 0 && -t 1 ]] || { echo "run_job.sh needs an interactive terminal (pulumi confirms up/destroy)"; exit 2; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$1"
SITE="$2"
STAMP="$(date -u +%Y%m%dt%H%M%Sz)"
RUN_ID="$(printf '%s-%s-%s' "${TARGET}" "${SITE}" "${STAMP}" | tr '_' '-')"
STACK=job
TIMEOUT_MIN="${MSV_TIMEOUT_MIN:-240}"
POLL_SEC="${MSV_POLL_SEC:-60}"
export AWS_PROFILE="${AWS_PROFILE:-claude-agent}" AWS_REGION=us-west-2
export MSV_TARGET="${TARGET}" MSV_SITE="${SITE}" MSV_RUN_ID="${RUN_ID}"
mkdir -p "${REPO}/logs"
LOG="${REPO}/logs/run_job-${RUN_ID}.log"

STARTED_EPOCH="$(date +%s)"
UP_ATTEMPTED=0
RESULT=1

say() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "${LOG}"; }
stack_output() { pulumi -C "${REPO}/infra/build_vm" stack output --stack "${STACK}" "$@"; }
pulumi_tty() { pulumi -C "${REPO}/infra/build_vm" "$@" --stack "${STACK}" </dev/tty >/dev/tty 2>&1; }

count_left() {
  local instances volumes
  instances="$(aws ec2 describe-instances --filters Name=tag:project,Values=mars-sv \
    Name=instance-state-name,Values=pending,running,stopping,shutting-down \
    --query 'length(Reservations)' --output text)"
  volumes="$(aws ec2 describe-volumes --filters Name=tag:project,Values=mars-sv \
    --query 'length(Volumes)' --output text)"
  say "left: instances(pending/running/stopping/shutting-down)=${instances} volumes=${volumes}"
  [[ "${instances}" == 0 && "${volumes}" == 0 ]]
}

teardown() {
  [[ "${UP_ATTEMPTED}" == 1 ]] || return 0
  say "pulumi destroy (confirm to tear down ${RUN_ID})"
  pulumi_tty destroy || say "WARNING: destroy failed or declined"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    count_left && break
    sleep 30
  done
  local hours
  hours="$(LC_ALL=C awk -v s="$(( $(date +%s) - STARTED_EPOCH ))" 'BEGIN { printf "%.2f", s / 3600 }')"
  say "wall time ${hours} h; add a docs/runs.md row (run_id ${RUN_ID})"
  say "log: ${LOG}"
}
trap teardown EXIT

instances_alive() {
  local ids
  ids="$(stack_output instance_ids --json | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)))')"
  [[ -n "${ids}" ]] || return 1
  local alive
  alive="$(aws ec2 describe-instances --instance-ids ${ids} \
    --query 'Reservations[].Instances[?State.Name!=`terminated`] | length(@[])' --output text 2>/dev/null || echo 0)"
  [[ "${alive}" != 0 ]]
}

poll_done() {
  local done_uri="$1" deadline=$(( $(date +%s) + TIMEOUT_MIN * 60 )) gone=0
  while (( $(date +%s) < deadline )); do
    if aws s3 cp --quiet "${done_uri}" "${REPO}/logs/DONE-${RUN_ID}.json" 2>/dev/null; then
      return 0
    fi
    if instances_alive; then gone=0; else gone=$(( gone + 1 )); fi
    (( gone >= 3 )) && { say "instance gone and no DONE (Spot interruption?)"; return 1; }
    say "waiting for ${done_uri}"
    sleep "${POLL_SEC}"
  done
  say "timed out after ${TIMEOUT_MIN} min"
  return 1
}

say "run ${RUN_ID}: target=${TARGET} site=${SITE}"
UP_ATTEMPTED=1
pulumi_tty up
ids="$(stack_output instance_ids --json)"
say "fleet $(stack_output fleet_id) instances ${ids} types $(stack_output instance_types --json | tr -d '\n ')"
[[ "${ids}" != "[]" ]] || { say "fleet launched no instance (Spot capacity or quota)"; exit 1; }

DONE_URI="$(stack_output done_uri)"
if poll_done "${DONE_URI}"; then
  tee -a "${LOG}" < "${REPO}/logs/DONE-${RUN_ID}.json"
  status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "${REPO}/logs/DONE-${RUN_ID}.json")"
  [[ "${status}" == 0 ]] && RESULT=0
  say "target ${TARGET} finished with status ${status}"
fi
aws logs tail /msv/build --log-stream-names "${RUN_ID}" --since 1d 2>/dev/null | tail -40 | tee -a "${LOG}" || true
exit "${RESULT}"
