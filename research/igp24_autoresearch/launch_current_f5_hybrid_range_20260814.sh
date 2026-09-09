#!/usr/bin/env bash
set -euo pipefail

plan=${1:?plan basename is required}
tag=${2:?output tag is required}
start=${3:?first group ordinal is required}
stop=${4:?last group ordinal is required}
parallel=${5:-6}
timeout_seconds=${6:-900}

cd "$(dirname "$0")"
if (( start < 1 || stop < start )); then
  echo "invalid ordinal range" >&2
  exit 2
fi

seq "$start" "$stop" | xargs -P "$parallel" -I{} bash -c '
  set -euo pipefail
  ordinal="$1"
  plan="$2"
  tag="$3"
  timeout_seconds="$4"
  output="data/current_f5_hybrid_value_${tag}_group${ordinal}_20260813.json"
  if [[ -f "$output" ]]; then
    exit 0
  fi
  timeout "$timeout_seconds" env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    /usr/bin/sage -python run_current_f5_hybrid_value_group_20260813.sage.py \
      --plan "$(basename "$plan")" --group-ordinal "$ordinal" --tag "$tag"
' _ {} "$plan" "$tag" "$timeout_seconds"
