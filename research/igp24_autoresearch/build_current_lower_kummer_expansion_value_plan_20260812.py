#!/usr/bin/env python3
"""Expand lower-Kummer action censuses over current tc0--tcN targets."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import build_current_lower_kummer_expansion_live_plan_20260812 as gold_plan


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--actions", action="append", required=True)
    parser.add_argument("--ignore-prior-plan", action="append", default=[])
    parser.add_argument("--maximum-sources-per-signature", type=int, default=4)
    parser.add_argument("--max-team-count", type=int, default=3)
    args = parser.parse_args()
    output = DATA / f"current_lower_kummer_expansion_{args.tag}_20260812.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if args.maximum_sources_per_signature <= 0 or args.max_team_count < 0:
        raise ValueError("invalid source or team-count bound")

    actions_by_group: dict[tuple[str, int], list[dict]] = defaultdict(list)
    action_paths: list[Path] = []
    seen_actions: set[str] = set()
    for value in args.actions:
        matches = sorted(DATA.glob(Path(value).name))
        if not matches:
            raise FileNotFoundError(value)
        for path in matches:
            action_paths.append(path)
            for action in gold_plan.jsonl(path):
                canonical = json.dumps(action, sort_keys=True, separators=(",", ":"))
                if canonical in seen_actions:
                    continue
                seen_actions.add(canonical)
                actions_by_group[
                    (str(action["sourceLabel"]), int(action.get("subsetSize", 2)))
                ].append(action)

    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    desired: dict[str, dict[int, int]] = defaultdict(dict)
    gold: dict[str, set[int]] = defaultdict(set)
    for row in connection.execute(
        """
        SELECT t.label,t.r,t.team_count
        FROM targets AS t
        LEFT JOIN baseline_pairs AS b USING(label,r)
        WHERE t.team_count<=? AND b.label IS NULL
          AND NOT EXISTS(
            SELECT 1 FROM verifications v
            WHERE v.label=t.label AND v.r=t.r AND v.status='accepted'
          )
        """,
        (args.max_team_count,),
    ):
        label = str(row["label"])
        r = int(row["r"])
        team_count = int(row["team_count"])
        desired[label][r] = team_count
        if team_count == 0:
            gold[label].add(r)

    excluded = gold_plan.prior_source_keys(
        {Path(value).name for value in args.ignore_prior_plan}
    )
    groups: list[dict] = []
    for (source_label, subset_size), actions in actions_by_group.items():
        if not any(desired[str(action["targetLabel"])] for action in actions):
            continue
        count_by_r: dict[int, int] = defaultdict(int)
        quotient_keys: set[str] = set()
        for source in connection.execute(
            """
            SELECT v.submission_id,v.polynomial_index,v.label,v.t,v.r,v.field_disc_abs,
                   p.coefficients,p.coefficient_hash,length(p.coefficients) AS coefficient_bytes
            FROM verifications v JOIN polynomials p USING(submission_id,polynomial_index)
            WHERE v.label=? AND v.status='accepted'
            ORDER BY coefficient_bytes,p.coefficient_hash
            """,
            (source_label,),
        ):
            coefficients = [int(value) for value in str(source["coefficients"]).split(",")]
            if (
                (str(source["coefficient_hash"]), subset_size) in excluded
                or len(coefficients) != 25
                or coefficients[-1] != 1
                or any(coefficients[index] for index in range(1, 24, 2))
                or count_by_r[int(source["r"])] >= args.maximum_sources_per_signature
            ):
                continue
            quotient_key = gold_plan.canonical_quotient_key(coefficients)
            if quotient_key in quotient_keys:
                continue
            routes = []
            for action in actions:
                target_label = str(action["targetLabel"])
                possible = {
                    int(value)
                    for value in action["sourceSignatureToPossibleTargetSignatures"].get(
                        str(source["r"]), []
                    )
                }
                useful = possible.intersection(desired[target_label])
                if useful:
                    routes.append(
                        {
                            "desiredR": sorted(useful),
                            "desiredTeamCounts": {
                                str(r): desired[target_label][r] for r in sorted(useful)
                            },
                            "goldR": sorted(gold[target_label].intersection(possible)),
                            "possibleR": sorted(possible),
                            "targetLabel": target_label,
                            "targetT": int(action["targetT"]),
                        }
                    )
            if not routes:
                continue
            count_by_r[int(source["r"])] += 1
            quotient_keys.add(quotient_key)
            quotient_line = ",".join(str(value) for value in coefficients[::2])
            groups.append(
                {
                    "actions": sorted(
                        actions,
                        key=lambda row: (
                            str(row["targetLabel"]),
                            json.dumps(row.get("subsetOrbit", row.get("pairOrbit"))),
                        ),
                    ),
                    "modeledDesiredFraction": max(
                        len(set(route["desiredR"])) / len(route["possibleR"])
                        for route in routes
                    ),
                    "quotientLine": quotient_line,
                    "quotientSha256": hashlib.sha256(quotient_line.encode()).hexdigest(),
                    "routes": routes,
                    "signatureAligned": any(
                        int(source["r"]) in route["desiredR"] for route in routes
                    ),
                    "source": {
                        "coefficientBytes": int(source["coefficient_bytes"]),
                        "coefficientSha256": str(source["coefficient_hash"]),
                        "fieldDiscAbs": str(source["field_disc_abs"]),
                        "label": str(source["label"]),
                        "polynomialIndex": int(source["polynomial_index"]),
                        "r": int(source["r"]),
                        "submissionId": str(source["submission_id"]),
                        "t": int(source["t"]),
                    },
                    "subsetSize": subset_size,
                }
            )
    connection.close()
    groups.sort(
        key=lambda group: (
            -int(bool(group["signatureAligned"])),
            -float(group["modeledDesiredFraction"]),
            min(
                tc
                for route in group["routes"]
                for tc in route["desiredTeamCounts"].values()
            ),
            int(group["subsetSize"]),
            int(group["source"]["coefficientBytes"]),
        )
    )
    for ordinal, group in enumerate(groups, 1):
        group["groupOrdinal"] = ordinal
    payload = {
        "actionFiles": [str(path.relative_to(ROOT)) for path in action_paths],
        "excludedPriorSourceSubsetKeys": len(excluded),
        "groupCount": len(groups),
        "groups": groups,
        "maximumSourcesPerSignature": args.maximum_sources_per_signature,
        "maximumTeamCount": args.max_team_count,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"groups": len(groups), "plan": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
