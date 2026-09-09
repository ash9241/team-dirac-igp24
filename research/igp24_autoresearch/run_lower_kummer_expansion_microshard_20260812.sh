#!/usr/bin/env bash
set -euo pipefail

shard="$1"
cd /path/to/igp24

subset_summary="data/current_lower_kummer_expansion_summary_micro${shard}_of256_20260812.json"
if [[ ! -f "$subset_summary" ]]; then
  env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 sage -python \
    agent_gold_c_lower_kummer_subset_product_action_census.sage.py \
    --action-map data/agent_gold_b_even_twist_action_map.jsonl \
    --historical "data/current_lower_kummer_expansion_input_micro${shard}_of256_20260812.jsonl" \
    --gold data/current_lower_kummer_expansion_gold_20260812.jsonl \
    --actions "data/current_lower_kummer_expansion_actions_micro${shard}_of256_20260812.jsonl" \
    --routes "data/current_lower_kummer_expansion_routes_micro${shard}_of256_20260812.jsonl" \
    --summary "$subset_summary" \
    --subset-sizes 3,4 \
    > "census_micro${shard}.log" 2>&1
fi

pair_summary="data/current_lower_kummer_expansion_pair_summary_micro${shard}_of256_20260812.json"
if [[ ! -f "$pair_summary" ]]; then
  env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 sage -python \
    agent_gold_c_lower_kummer_pair_product_action_census.sage.py \
    --action-map data/agent_gold_b_even_twist_action_map.jsonl \
    --historical "data/current_lower_kummer_expansion_input_micro${shard}_of256_20260812.jsonl" \
    --gold data/current_lower_kummer_expansion_gold_20260812.jsonl \
    --actions "data/current_lower_kummer_expansion_pair_actions_micro${shard}_of256_20260812.jsonl" \
    --routes "data/current_lower_kummer_expansion_pair_routes_micro${shard}_of256_20260812.jsonl" \
    --summary "$pair_summary" \
    > "pair_micro${shard}.log" 2>&1
fi
