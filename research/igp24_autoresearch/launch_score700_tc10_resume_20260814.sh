#!/usr/bin/env bash
set -euo pipefail

plan=data/current_score700_signature_recovery_tc10_plan_20260813.jsonl
targets=data/current_score700_signature_recovery_tc10_targets_20260813.json
indices=data/score700_tc10_missing_indices_20260814.txt
output_dir=data/score700_tc10_resume_20260814
log_dir=data/score700_tc10_resume_logs_20260814

mkdir -p "$output_dir" "$log_dir"
xargs -P8 -I{} bash -c '
  set -euo pipefail
  index="$1"
  timeout 1800 env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    /usr/bin/sage -python run_current_score700_signature_recovery_job_20260813.py \
      --plan data/current_score700_signature_recovery_tc10_plan_20260813.jsonl \
      --targets data/current_score700_signature_recovery_tc10_targets_20260813.json \
      --job-index "$index" \
      --output-dir data/score700_tc10_resume_20260814 \
      --options-per-signature 12 \
      --prime-limit 30000 \
      --timeout 1500 \
      > "data/score700_tc10_resume_logs_20260814/job_${index}.log" 2>&1
' _ {} < "$indices"
