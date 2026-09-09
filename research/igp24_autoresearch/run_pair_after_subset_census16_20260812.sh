#!/usr/bin/env bash
set -euo pipefail

first_shard="$1"
last_shard="$2"
cd /path/to/igp24

for ((shard = first_shard; shard <= last_shard; shard++)); do
  nohup bash -c '
    shard="$1"
    while [[ ! -f "data/current_lower_kummer_expansion_summary_shard${shard}_of16_20260812.json" ]]; do
      sleep 5
    done
    exec env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 sage -python \
      agent_gold_c_lower_kummer_pair_product_action_census.sage.py \
      --action-map data/agent_gold_b_even_twist_action_map.jsonl \
      --historical "data/current_lower_kummer_expansion_input_shard${shard}_of16_20260812.jsonl" \
      --gold data/current_lower_kummer_expansion_gold_20260812.jsonl \
      --actions "data/current_lower_kummer_expansion_pair_actions_shard${shard}_of16_20260812.jsonl" \
      --routes "data/current_lower_kummer_expansion_pair_routes_shard${shard}_of16_20260812.jsonl" \
      --summary "data/current_lower_kummer_expansion_pair_summary_shard${shard}_of16_20260812.json"
  ' pair-census "$shard" > "pair16_shard${shard}.log" 2>&1 < /dev/null &
done
