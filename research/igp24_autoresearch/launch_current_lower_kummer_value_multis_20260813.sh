#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
plan="data/current_lower_kummer_expansion_value_tc3_wave1_20260812.json"
tag=${1:-valuetc0multi}
start=${2:-1}
end=${3:-1905}
maximum_team_count=${4:-0}
parallel=${5:-8}
timeout_seconds=${6:-900}

python3 - "$plan" "$start" "$end" "$maximum_team_count" "$tag" <<'PY' |
import json
import sys
from pathlib import Path

plan_path = Path(sys.argv[1])
start = int(sys.argv[2])
end = int(sys.argv[3])
maximum_team_count = int(sys.argv[4])
tag = sys.argv[5]
payload = json.loads(plan_path.read_text(encoding="utf-8"))
for group in payload["groups"]:
    ordinal = int(group["groupOrdinal"])
    output = Path("data") / f"current_lower_kummer_expansion_{tag}_group{ordinal}_20260812.json"
    desired_counts = [
        int(team_count)
        for route in group["routes"]
        for team_count in route["desiredTeamCounts"].values()
    ]
    if (
        start <= ordinal <= end
        and len(group["actions"]) > 1
        and desired_counts
        and min(desired_counts) <= maximum_team_count
        and not output.exists()
    ):
        print(ordinal)
PY
  xargs -P "$parallel" -I{} timeout "$timeout_seconds" \
    env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    /usr/bin/sage -python run_current_lower_kummer_expansion_exact_group_20260812.sage.py \
    --plan "$(basename "$plan")" \
    --group-ordinal {} \
    --tag "$tag" \
    --assignment-prime-bound 10000
