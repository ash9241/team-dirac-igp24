#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs

run_one() {
  local q=$1
  local shard=$2
  local targets=()
  case "$q" in
    141)
      targets=(24T11603 24T11605 24T13452 24T13460 24T17425 24T18699)
      ;;
    222)
      targets=(24T11966 24T15900 24T17652 24T18990 24T20003 24T20915 24T20923 24T20940 24T20945 24T20949 24T21920)
      ;;
    260)
      targets=(24T18327 24T20491)
      ;;
    *)
      echo "unknown quotient lane: $q" >&2
      return 2
      ;;
  esac

  local census="data/current_squareclass_q${q}_census_shard$(printf '%02d' "$shard")_of08_20260812.json"
  local output="data/current_squareclass_q${q}_local_shard$(printf '%02d' "$shard")_of08_20260812.json"
  local log="logs/current_squareclass_q${q}_local_shard$(printf '%02d' "$shard")_of08_20260812.log"
  [[ -s "$output" ]] && return 0

  local target_args=()
  local target
  for target in "${targets[@]}"; do
    target_args+=(--target-label "$target")
  done
  env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 /usr/bin/sage -python \
    broad_structural_character_gate_squareclass_20260731.sage.py \
    --quotient-t "$q" \
    "${target_args[@]}" \
    --census "$census" \
    --db "data/current_squareclass_q${q}_gate_ledger_20260812.sqlite3" \
    --phase local \
    --output "$output" >"$log" 2>&1
}

export -f run_one
printf '%s\n' \
  141:{0..7} \
  222:{0..7} \
  260:{1..7} |
  tr ':' ' ' |
  xargs -P 12 -n 2 bash -c 'run_one "$0" "$1"'

