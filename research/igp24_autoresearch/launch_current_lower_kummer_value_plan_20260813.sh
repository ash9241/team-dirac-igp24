#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
plan=${1:?plan basename is required}
tag=${2:?output tag is required}
mode=${3:-singleton}
start=${4:-1}
end=${5:-999999}
maximum_team_count=${6:-3}
parallel=${7:-16}
timeout_seconds=${8:-900}
maximum_action_count=${9:-999999}

python3 - "$plan" "$tag" "$mode" "$start" "$end" "$maximum_team_count" "$maximum_action_count" <<'PY' |
import json
import sys
from pathlib import Path

plan_path = Path("data") / Path(sys.argv[1]).name
tag = sys.argv[2]
mode = sys.argv[3]
start = int(sys.argv[4])
end = int(sys.argv[5])
maximum_team_count = int(sys.argv[6])
maximum_action_count = int(sys.argv[7])
if mode not in {"singleton", "multi", "all"}:
    raise ValueError("mode must be singleton, multi, or all")
payload = json.loads(plan_path.read_text(encoding="utf-8"))
for group in payload["groups"]:
    ordinal = int(group["groupOrdinal"])
    action_count = len(group["actions"])
    desired_counts = [
        int(team_count)
        for route in group["routes"]
        for team_count in route["desiredTeamCounts"].values()
    ]
    output = Path("data") / (
        f"current_lower_kummer_expansion_{tag}_group{ordinal}_20260812.json"
    )
    mode_matches = (
        mode == "all"
        or (mode == "singleton" and action_count == 1)
        or (mode == "multi" and action_count > 1)
    )
    if (
        start <= ordinal <= end
        and mode_matches
        and desired_counts
        and min(desired_counts) <= maximum_team_count
        and action_count <= maximum_action_count
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
