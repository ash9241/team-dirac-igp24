#!/usr/bin/env bash
set -euo pipefail

plan=${1:?plan jsonl is required}
targets=${2:?targets json is required}
tag=${3:-score700sig}
workers=${4:-16}
timeout_seconds=${5:-1200}
options_per_signature=${6:-12}
prime_limit=${7:-30000}

output_dir="data/current_score700_signature_recovery_${tag}_outputs_20260813"
log_dir="data/current_score700_signature_recovery_${tag}_logs_20260813"
mkdir -p "$output_dir" "$log_dir"

job_count=$(wc -l < "$plan" | tr -d ' ')
seq 0 $((job_count - 1)) | xargs -P "$workers" -I{} bash -c '
  index="$1"
  output_dir="$2"
  log_dir="$3"
  plan="$4"
  targets="$5"
  timeout_seconds="$6"
  options="$7"
  prime_limit="$8"
  existing=$(find "$output_dir" -maxdepth 1 -name "job_$(printf "%04d" "$index")_*.json" -print -quit)
  if [[ -n "$existing" ]]; then
    exit 0
  fi
  timeout "$timeout_seconds" env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    /usr/bin/sage -python run_current_score700_signature_recovery_job_20260813.py \
      --plan "$plan" \
      --targets "$targets" \
      --job-index "$index" \
      --output-dir "$output_dir" \
      --timeout "$timeout_seconds" \
      --options-per-signature "$options" \
      --prime-limit "$prime_limit" \
      > "$log_dir/job_$(printf "%04d" "$index").log" 2>&1
' _ {} "$output_dir" "$log_dir" "$plan" "$targets" "$timeout_seconds" "$options_per_signature" "$prime_limit"
