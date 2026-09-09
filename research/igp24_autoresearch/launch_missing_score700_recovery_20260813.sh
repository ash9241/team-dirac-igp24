#!/usr/bin/env bash
set -euo pipefail

plan=${1:?plan jsonl is required}
targets=${2:?targets json is required}
output_dir=${3:?output directory is required}
log_dir=${4:?log directory is required}
workers=${5:-4}
timeout_seconds=${6:-1200}
options_per_signature=${7:-12}
prime_limit=${8:-30000}

mkdir -p "$output_dir" "$log_dir"
job_count=$(wc -l < "$plan" | tr -d ' ')

seq 0 $((job_count - 1)) | xargs -P "$workers" -I{} bash -c '
  set -euo pipefail
  index="$1"
  plan="$2"
  targets="$3"
  output_dir="$4"
  log_dir="$5"
  timeout_seconds="$6"
  options="$7"
  prime_limit="$8"
  prefix="job_$(printf "%04d" "$index")_"
  if find "$output_dir" -maxdepth 1 -name "${prefix}*.json" -print -quit | grep -q .; then
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
      > "$log_dir/$(printf "job_%04d.log" "$index")" 2>&1
' _ {} "$plan" "$targets" "$output_dir" "$log_dir" "$timeout_seconds" "$options_per_signature" "$prime_limit"
