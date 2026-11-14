#!/bin/bash
set -uo pipefail

RUN_ID="@@RUN_ID@@"
TARGET="@@TARGET@@"
SITE="@@SITE@@"
IMAGE="@@IMAGE@@"
BUCKET="@@BUCKET@@"
REGION="@@REGION@@"
LOG_GROUP="@@LOG_GROUP@@"
HARD_STOP_MIN="@@HARD_STOP_MIN@@"
WORK_URI="s3://${BUCKET}/work/${RUN_ID}"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STATUS=1
STEP=boot

shutdown -h "+${HARD_STOP_MIN}" "msv hard stop"

finish() {
  local finished
  finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  aws s3 cp --region "${REGION}" /var/log/cloud-init-output.log "${WORK_URI}/logs/cloud-init-output.log" || true
  printf '{"run_id":"%s","target":"%s","site":"%s","image":"%s","status":%d,"step":"%s","started":"%s","finished":"%s"}\n' \
    "${RUN_ID}" "${TARGET}" "${SITE}" "${IMAGE}" "${STATUS}" "${STEP}" "${STARTED}" "${finished}" > /tmp/DONE
  aws s3 cp --region "${REGION}" /tmp/DONE "${WORK_URI}/DONE" || true
  shutdown -h now "msv run finished"
}
trap finish EXIT

STEP=docker-install
dnf install -y docker || exit 1
systemctl enable --now docker || exit 1

STEP=docker-pull
for attempt in 1 2 3; do
  docker pull "${IMAGE}" && break
  [[ ${attempt} == 3 ]] && exit 1
  sleep 20
done

STEP=run
mkdir -p /work
docker run --rm --name msv-run \
  --log-driver awslogs --log-opt "awslogs-region=${REGION}" \
  --log-opt "awslogs-group=${LOG_GROUP}" --log-opt "awslogs-stream=${RUN_ID}" \
  -e AWS_REGION="${REGION}" -e MSV_SITE="${SITE}" -e MSV_RUN_ID="${RUN_ID}" \
  -e MSV_BUCKET="${BUCKET}" -e MSV_IMAGE="${IMAGE}" \
  -v /work:/work "${IMAGE}" run "${TARGET}"
STATUS=$?
STEP=done
