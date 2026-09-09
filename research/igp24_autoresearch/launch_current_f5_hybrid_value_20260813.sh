#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
plan=${1:?plan basename is required}
tag=${2:?output tag is required}
parallel=${3:-16}
timeout_seconds=${4:-900}

python3 - "$plan" "$tag" <<'PY' |
import json
import sys
from pathlib import Path

plan = json.loads((Path("data") / Path(sys.argv[1]).name).read_text(encoding="utf-8"))
tag = sys.argv[2]
for group in plan["groups"]:
    ordinal = int(group["groupOrdinal"])
    output = Path("data") / f"current_f5_hybrid_value_{tag}_group{ordinal}_20260813.json"
    if not output.exists():
        print(ordinal)
PY
  xargs -P "$parallel" -I{} timeout "$timeout_seconds" \
    env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    /usr/bin/sage -python run_current_f5_hybrid_value_group_20260813.sage.py \
    --plan "$(basename "$plan")" --group-ordinal {} --tag "$tag"
