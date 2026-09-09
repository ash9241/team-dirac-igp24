#!/usr/bin/env bash
set -euo pipefail

start="$1"
stop="$2"
workers="${3:-8}"
cd /path/to/igp24
seq "$start" "$stop" | xargs -P "$workers" -I{} \
  bash run_lower_kummer_expansion_k711_microshard_20260812.sh {}
