#!/usr/bin/env python3
"""Freeze fresh unique-action lower-Kummer pair-product routes."""

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
ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
PLAN = DATA / "current_lower_kummer_pair_wave_20260812.json"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def tried_hashes() -> set[str]:
    values = set()
    for path in DATA.glob("agent_gold_c_lower_kummer_pair_product_*results.jsonl"):
        for row in rows(path):
            value = (row.get("source") or {}).get("coefficientSha256")
            if value:
                values.add(str(value))
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=PLAN)
    args = parser.parse_args()
    if args.plan.exists():
        raise FileExistsError(f"refusing to overwrite {args.plan}")
    actions = rows(ACTIONS)
    multiplicity = Counter(str(row["sourceLabel"]) for row in actions)
    unique_actions = {
        str(row["sourceLabel"]): row
        for row in actions
        if multiplicity[str(row["sourceLabel"])] == 1
    }
    excluded = tried_hashes()
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    gold_by_label: dict[str, set[int]] = defaultdict(set)
    for row in connection.execute(
        """
        SELECT t.label,t.r FROM targets t
        LEFT JOIN baseline_pairs b USING(label,r)
        WHERE t.team_count=0 AND t.discovered=0 AND b.label IS NULL
          AND NOT EXISTS(
            SELECT 1 FROM verifications v
            WHERE v.label=t.label AND v.r=t.r AND v.status='accepted'
          )
        """
    ):
        gold_by_label[str(row["label"])].add(int(row["r"]))

    routes = []
    for source_label, action in unique_actions.items():
        gold_r = gold_by_label.get(str(action["targetLabel"]), set())
        if not gold_r:
            continue
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
            possible = {
                int(value)
                for value in action["sourceSignatureToPossibleTargetSignatures"].get(
                    str(source["r"]), []
                )
            }
            if (
                str(source["coefficient_hash"]) in excluded
                or len(coefficients) != 25
                or coefficients[-1] != 1
                or any(coefficients[index] for index in range(1, 24, 2))
                or not possible.intersection(gold_r)
            ):
                continue
            routes.append(
                {
                    "action": action,
                    "goldR": sorted(gold_r),
                    "possibleR": sorted(possible),
                    "source": {
                        "coefficientBytes": int(source["coefficient_bytes"]),
                        "coefficientSha256": str(source["coefficient_hash"]),
                        "label": str(source["label"]),
                        "polynomialIndex": int(source["polynomial_index"]),
                        "r": int(source["r"]),
                        "submissionId": str(source["submission_id"]),
                    },
                }
            )
    connection.close()

    deduplicated = {}
    for route in routes:
        source = route["source"]
        key = (source["coefficientSha256"], str(route["action"]["targetLabel"]))
        deduplicated.setdefault(key, route)
    routes = list(deduplicated.values())
    routes.sort(
        key=lambda row: (
            -len(set(row["goldR"]).intersection(row["possibleR"])) / len(row["possibleR"]),
            row["source"]["coefficientBytes"],
            row["action"]["targetT"],
            row["source"]["r"],
            row["source"]["coefficientSha256"],
        )
    )
    first, remainder, covered = [], [], set()
    for route in routes:
        target = str(route["action"]["targetLabel"])
        (first if target not in covered else remainder).append(route)
        covered.add(target)
    routes = first + remainder
    for ordinal, route in enumerate(routes, 1):
        route["routeOrdinal"] = ordinal

    payload = {
        "actionsPath": str(ACTIONS.relative_to(ROOT)),
        "actionsSha256": hashlib.sha256(ACTIONS.read_bytes()).hexdigest(),
        "excludedPriorSourceHashes": len(excluded),
        "mechanism": "exact-conjugate-pair-product-unique-action-v1",
        "networkCalls": 0,
        "routeCount": len(routes),
        "submissionCalls": 0,
        "targetLabels": sorted(covered, key=lambda value: int(value[3:])),
        "routes": routes,
    }
    args.plan.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"plan": str(args.plan), "routes": len(routes), "targets": len(covered)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
