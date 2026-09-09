#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
plan="data/current_lower_kummer_expansion_value_tc3_wave1_20260812.json"
tag=${1:-valuetc3b}
start=${2:-30}
end=${3:-600}
parallel=${4:-16}

python3 - "$plan" "$start" "$end" "$tag" <<'PY' |
import json
import sys
from pathlib import Path

plan_path = Path(sys.argv[1])
start = int(sys.argv[2])
end = int(sys.argv[3])
tag = sys.argv[4]
payload = json.loads(plan_path.read_text(encoding="utf-8"))
for group in payload["groups"]:
    ordinal = int(group["groupOrdinal"])
    output = Path("data") / f"current_lower_kummer_expansion_{tag}_group{ordinal}_20260812.json"
    if start <= ordinal <= end and len(group["actions"]) == 1 and not output.exists():
        print(ordinal)
PY
  xargs -P "$parallel" -I{} env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    /usr/bin/sage -python run_current_lower_kummer_expansion_exact_group_20260812.sage.py \
    --plan "$(basename "$plan")" \
    --group-ordinal {} \
    --tag "$tag" \
    --assignment-prime-bound 10000

