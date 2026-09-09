#!/usr/bin/env python3
"""Freeze one fresh source per live lower-Kummer subset source/signature."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl"
PLAN = DATA / "current_lower_kummer_subset_wave_20260812.json"


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--multiorbit-only", action="store_true")
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    plan_path = args.plan or (
        DATA / "current_lower_kummer_subset_multiorbit_wave_20260812.json"
        if args.multiorbit_only
        else PLAN
    )
    if plan_path.exists():
        raise FileExistsError(f"refusing to overwrite {plan_path}")
    prior_group_keys = set()
    if args.multiorbit_only and PLAN.exists():
        prior = json.loads(PLAN.read_text())
        prior_group_keys = {
            (
                str(group["source"]["label"]),
                int(group["source"]["r"]),
                int(group["subsetSize"]),
            )
            for group in prior["groups"]
        }
    tried = set()
    for pattern in ("agent_f9_*results.jsonl", "rank11_k3*results.jsonl"):
        for path in DATA.glob(pattern):
            for row in load(path):
                value = (row.get("source") or {}).get("coefficientSha256")
                if value:
                    tried.add(str(value))
    actions_by_group = defaultdict(list)
    for action in load(ACTIONS):
        actions_by_group[(str(action["sourceLabel"]), int(action["subsetSize"]))].append(action)

    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    gold = defaultdict(set)
    for row in connection.execute(
        """
        SELECT t.label,t.r FROM targets t LEFT JOIN baseline_pairs b USING(label,r)
        WHERE t.team_count=0 AND t.discovered=0 AND b.label IS NULL
          AND NOT EXISTS(
            SELECT 1 FROM verifications v
            WHERE v.label=t.label AND v.r=t.r AND v.status='accepted'
          )
        """
    ):
        gold[str(row["label"])].add(int(row["r"]))

    groups = []
    for (source_label, subset_size), actions in actions_by_group.items():
        target_multiplicity = Counter(str(action["targetLabel"]) for action in actions)
        route_actions = [
            action for action in actions
            if (args.multiorbit_only or target_multiplicity[str(action["targetLabel"])] == 1)
            and gold[str(action["targetLabel"])]
        ]
        if not route_actions:
            continue
        best_by_r = {}
        for source in connection.execute(
            """
            SELECT v.submission_id,v.polynomial_index,v.label,v.r,
                   p.coefficients,p.coefficient_hash,length(p.coefficients) AS coefficient_bytes
            FROM verifications v JOIN polynomials p USING(submission_id,polynomial_index)
            WHERE v.label=? AND v.status='accepted'
            ORDER BY coefficient_bytes,p.coefficient_hash
            """,
            (source_label,),
        ):
            coefficients = [int(value) for value in str(source["coefficients"]).split(",")]
            if (
                str(source["coefficient_hash"]) in tried
                or len(coefficients) != 25
                or coefficients[-1] != 1
                or any(coefficients[index] for index in range(1, 24, 2))
            ):
                continue
            routes = []
            for action in route_actions:
                possible = {
                    int(value)
                    for value in action["sourceSignatureToPossibleTargetSignatures"].get(
                        str(source["r"]), []
                    )
                }
                target_gold = gold[str(action["targetLabel"])]
                if possible.intersection(target_gold):
                    routes.append(
                        {
                            "goldR": sorted(target_gold),
                            "possibleR": sorted(possible),
                            "targetLabel": str(action["targetLabel"]),
                        }
                    )
            if not routes or int(source["r"]) in best_by_r:
                continue
            group_key = (source_label, int(source["r"]), subset_size)
            if group_key in prior_group_keys:
                continue
            best_by_r[int(source["r"])] = {
                "actions": sorted(
                    actions,
                    key=lambda row: (str(row["targetLabel"]), json.dumps(row["subsetOrbit"])),
                ),
                "routes": routes,
                "source": {
                    "coefficientBytes": int(source["coefficient_bytes"]),
                    "coefficientSha256": str(source["coefficient_hash"]),
                    "label": str(source["label"]),
                    "polynomialIndex": int(source["polynomial_index"]),
                    "r": int(source["r"]),
                    "submissionId": str(source["submission_id"]),
                },
                "subsetSize": subset_size,
            }
        groups.extend(best_by_r.values())
    connection.close()
    groups.sort(
        key=lambda group: (
            -max(
                len(set(route["goldR"]).intersection(route["possibleR"])) / len(route["possibleR"])
                for route in group["routes"]
            ),
            group["subsetSize"],
            group["source"]["coefficientBytes"],
            group["source"]["label"],
            group["source"]["r"],
        )
    )
    for ordinal, group in enumerate(groups, 1):
        group["groupOrdinal"] = ordinal
    payload = {
        "actionsPath": str(ACTIONS.relative_to(ROOT)),
        "actionsSha256": hashlib.sha256(ACTIONS.read_bytes()).hexdigest(),
        "groupCount": len(groups),
        "mechanism": "joint-archimedean-and-modular-subset-factor-assignment-v1",
        "networkCalls": 0,
        "submissionCalls": 0,
        "groups": groups,
    }
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"groups": len(groups), "plan": str(plan_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
