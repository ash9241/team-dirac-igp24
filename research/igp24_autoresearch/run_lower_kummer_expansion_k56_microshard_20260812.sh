#!/usr/bin/env bash
set -euo pipefail

shard="$1"
cd /path/to/igp24
summary="data/current_lower_kummer_expansion_k56_summary_micro${shard}_of256_20260812.json"
if [[ -f "$summary" ]]; then
  exit 0
fi

env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 sage -python \
  agent_gold_c_lower_kummer_subset_product_action_census.sage.py \
  --action-map data/agent_gold_b_even_twist_action_map.jsonl \
  --historical "data/current_lower_kummer_expansion_input_micro${shard}_of256_20260812.jsonl" \
  --gold data/current_lower_kummer_expansion_gold_20260812.jsonl \
  --actions "data/current_lower_kummer_expansion_k56_actions_micro${shard}_of256_20260812.jsonl" \
  --routes "data/current_lower_kummer_expansion_k56_routes_micro${shard}_of256_20260812.jsonl" \
  --summary "$summary" \
  --subset-sizes 5,6 \
  > "k56_micro${shard}.log" 2>&1
