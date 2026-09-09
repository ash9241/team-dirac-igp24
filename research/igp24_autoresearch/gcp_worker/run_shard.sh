#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 SHARD_INDEX SHARD_COUNT" >&2
  exit 2
fi

shard_index=$1
shard_count=$2
max_parallel=${IGP24_MAX_PARALLEL:-9}
job_timeout=${IGP24_JOB_TIMEOUT:-3h}
jobs_file=${IGP24_JOBS_FILE:-jobs.tsv}
ledger_db=${IGP24_DB:-data/ledger.sqlite3}
pari_stack_bytes=${IGP24_PARI_STACK_BYTES:-4294967296}
pari_stack_max_bytes=${IGP24_PARI_STACK_MAX_BYTES:-17179869184}

if (( shard_index < 0 || shard_index >= shard_count )); then
  echo "invalid shard ${shard_index}/${shard_count}" >&2
  exit 2
fi

cd "$(dirname "$0")"
sha256sum --check MANIFEST.sha256
command -v sage >/dev/null

mkdir -p logs outputs status

run_job() {
  local job_id=$1
  local quotient_t=$2
  local target_labels=$3
  local census=$4
  local field_hash=$5
  local output="outputs/${job_id}.json"
  local log="logs/${job_id}.log"
  local status_file="status/${job_id}.status"
  local -a target_args=()
  local target_label

  if [[ -s "$output" ]]; then
    printf 'complete-existing\n' > "$status_file"
    return 0
  fi

  IFS=',' read -r -a labels <<< "$target_labels"
  for target_label in "${labels[@]}"; do
    target_args+=(--target-label "$target_label")
  done

  set +e
  timeout --signal=INT --kill-after=60s "$job_timeout" \
    nice -n 5 sage -python \
    broad_structural_character_gate_squareclass_20260731.sage.py \
    --quotient-t "$quotient_t" \
    "${target_args[@]}" \
    --census "$census" \
    --db "$ledger_db" \
    --phase exact \
    --field-hash "$field_hash" \
    --witness-primes 10000 \
    --max-reconstructions 1 \
    --max-coset-reconstructions-per-sign 16 \
    --pari-stack-bytes "$pari_stack_bytes" \
    --pari-stack-max-bytes "$pari_stack_max_bytes" \
    --output "$output" > "$log" 2>&1
  exit_code=$?
  set -e
  printf '%s\n' "$exit_code" > "$status_file"
}

active=0
line_number=0
while IFS=$'\t' read -r job_id quotient_t target_labels census field_hash; do
  [[ -z "$job_id" || "$job_id" == \#* ]] && continue
  if (( line_number % shard_count == shard_index )); then
    run_job "$job_id" "$quotient_t" "$target_labels" "$census" "$field_hash" &
    ((active += 1))
    if (( active >= max_parallel )); then
      wait -n || true
      ((active -= 1))
    fi
  fi
  ((line_number += 1))
done < "$jobs_file"

wait
date -u +%Y-%m-%dT%H:%M:%SZ > SHARD_COMPLETE
