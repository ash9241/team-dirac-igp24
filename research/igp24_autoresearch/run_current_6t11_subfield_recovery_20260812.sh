#!/usr/bin/env bash
set -euo pipefail

cd /path/to/igp24
mkdir -p data

active=0
for shard in 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15; do
  input="data/current_6t11_subfield_tasks_shard_${shard}"
  output="data/current_6t11_subfields_${shard}.jsonl"
  log="subfields_${shard}.log"
  /usr/bin/sage -python agent_non12_subfield_worker.sage.py \
    --input "$input" --output "$output" > "$log" 2>&1 &
  active=$((active + 1))
  if (( active == 4 )); then
    wait
    active=0
  fi
done
wait
