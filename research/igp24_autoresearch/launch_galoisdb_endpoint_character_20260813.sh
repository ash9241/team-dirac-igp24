#!/usr/bin/env bash
set -euo pipefail

plan=${1:?plan required}
tag=${2:-endpoint}
workers=${3:-16}
timeout_seconds=${4:-1200}
options=${5:-16}
prime_limit=${6:-30000}
output_dir="data/galoisdb_endpoint_character_${tag}_outputs_20260813"
log_dir="data/galoisdb_endpoint_character_${tag}_logs_20260813"
mkdir -p "$output_dir" "$log_dir"
jobs=$(wc -l < "$plan" | tr -d ' ')
seq 0 $((jobs - 1)) | xargs -P "$workers" -I{} bash -c '
  index="$1"; output_dir="$2"; log_dir="$3"; plan="$4"; timeout_seconds="$5"; options="$6"; prime_limit="$7"
  existing=$(find "$output_dir" -maxdepth 1 -name "job_$(printf "%04d" "$index")_*.json" -print -quit)
  [[ -n "$existing" ]] && exit 0
  timeout "$timeout_seconds" env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    /usr/bin/sage -python run_galoisdb_endpoint_character_job_20260813.py \
      --plan "$plan" --job-index "$index" --output-dir "$output_dir" \
      --timeout "$timeout_seconds" --options-per-signature "$options" \
      --prime-limit "$prime_limit" \
      > "$log_dir/job_$(printf "%04d" "$index").log" 2>&1
' _ {} "$output_dir" "$log_dir" "$plan" "$timeout_seconds" "$options" "$prime_limit"
