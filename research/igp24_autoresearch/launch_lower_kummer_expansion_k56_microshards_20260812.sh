#!/usr/bin/env bash
set -euo pipefail

first_shard="$1"
last_shard="$2"
cd /path/to/igp24

seq "$first_shard" "$last_shard" | xargs -P 8 -n 1 \
  bash run_lower_kummer_expansion_k56_microshard_20260812.sh
