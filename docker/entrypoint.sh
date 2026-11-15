#!/usr/bin/env bash
set -euo pipefail

if [[ -d /opt/conda/envs/isis ]]; then
  export ISISROOT=/opt/conda/envs/isis ISISDATA="${ISISDATA:-/isisdata}"
fi

usage() {
  cat <<'TXT'
mars-sv image
  run <target>   snakemake --cores all <target>, then sync /work/<run_id> to s3://$MSV_BUCKET/work/<run_id>/
                 env: MSV_SITE (required), MSV_RUN_ID (default: UTC timestamp), MSV_BUCKET, MSV_SYNC (1/0)
  <command...>   run any command in the image (python, pc_align, dem_geoid, snakemake, ...)
  ISIS apps exist only in images built with WITH_ISIS=1 (tag suffix -isis): /opt/conda/envs/isis/bin
TXT
}

run_target() {
  local target="$1"
  : "${MSV_SITE:?MSV_SITE is required}"
  local run_id="${MSV_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
  local workdir="${MSV_WORK}/${run_id}"
  mkdir -p "${workdir}"
  local status=0
  snakemake --snakefile /opt/msv/pipelines/Snakefile --directory "${workdir}" --cores all \
    --rerun-incomplete --config site="${MSV_SITE}" run_id="${run_id}" bucket="${MSV_BUCKET}" \
    -- "${target}" || status=$?
  if [[ "${MSV_SYNC:-1}" == "1" ]]; then
    python -m pipelines.sync_work "${workdir}" "s3://${MSV_BUCKET}/work/${run_id}/"
  fi
  return "${status}"
}

case "${1:-}" in
  ""|-h|--help) usage ;;
  run)
    [[ $# -eq 2 ]] || { usage; exit 2; }
    cd /opt/msv && run_target "$2"
    ;;
  *) exec "$@" ;;
esac
