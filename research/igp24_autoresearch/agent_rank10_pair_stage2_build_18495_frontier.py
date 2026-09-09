#!/usr/bin/env python3
"""Build the deterministic disjoint 24T18495 presentation frontier."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import agent_rank10_pair_stage2 as stage2


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "agent_rank10_pair_stage2_18495_frontier.jsonl"
SUMMARY = ROOT / "data" / "agent_rank10_pair_stage2_18495_frontier_summary.json"
PROFILE = ROOT / "data" / "agent_rank10_pair_stage2_profiles.jsonl"

DESIRED_R = {8: 14, 20: 20, 12: 18}


def main() -> int:
    profiles = {
        str(row["sourceLabel"]): row for row in stage2.read_jsonl(PROFILE)
    }
    profile = profiles["24T18495"]
    tested = stage2.tested_source_keys()
    rows_by_r: dict[int, dict[str, dict]] = defaultdict(dict)
    tested_discs: dict[int, set[str]] = defaultdict(set)
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """
            SELECT v.submission_id,v.polynomial_index,v.r,v.field_disc_abs,
                   p.coefficient_hash,length(p.original_line)
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.scoreable=1 AND v.label='24T18495' AND v.r IN (8,12,20)
            ORDER BY v.r,v.field_disc_abs,length(p.original_line),
                     v.submission_id,v.polynomial_index
            """
        ).fetchall()
    for submission, index, source_r, disc, digest, coefficient_bytes in rows:
        key = (str(submission), int(index))
        source_r = int(source_r)
        if key in tested:
            tested_discs[source_r].add(str(disc))
            continue
        rows_by_r[source_r].setdefault(
            str(digest),
            {
                "submissionId": key[0],
                "polynomialIndex": key[1],
                "sourceLabel": "24T18495",
                "sourceT": 18495,
                "sourceR": source_r,
                "sourceFieldDiscAbs": str(disc),
                "sourceCoefficientSha256": str(digest),
                "sourceCoefficientBytes": int(coefficient_bytes),
                "targetLabel": "24T18861",
                "desiredTargetR": DESIRED_R[source_r],
            },
        )

    compatible = {}
    for source_r, desired_r in DESIRED_R.items():
        classes = [
            row
            for row in profile["profiles"]
            if int(row["sourceR"]) == source_r
        ]
        indexes = []
        numerator = 0
        denominator = sum(int(row["classSize"]) for row in classes)
        for row in classes:
            if any(
                str(item["targetLabel"]) == "24T18861"
                and int(item["targetR"]) == desired_r
                for item in row["orbitSignatures"]
            ):
                indexes.append(int(row["classIndex"]))
                numerator += int(row["classSize"])
        compatible[source_r] = {
            "indexes": indexes,
            "numerator": numerator,
            "denominator": denominator,
            "fraction": numerator / denominator,
        }

    unseen_first = []
    remainder = []
    for source_r, by_hash in rows_by_r.items():
        by_disc: dict[str, list[dict]] = defaultdict(list)
        for row in by_hash.values():
            row.update(
                {
                    "compatibleClassIndexes": compatible[source_r]["indexes"],
                    "compatibleClassSizeNumerator": compatible[source_r]["numerator"],
                    "compatibleClassSizeDenominator": compatible[source_r]["denominator"],
                    "compatibleClassSizeFraction": compatible[source_r]["fraction"],
                }
            )
            by_disc[row["sourceFieldDiscAbs"]].append(row)
        for disc, disc_rows in by_disc.items():
            disc_rows.sort(
                key=lambda row: (
                    int(row["sourceCoefficientBytes"]),
                    row["submissionId"],
                    int(row["polynomialIndex"]),
                )
            )
            if disc not in tested_discs[source_r]:
                first, *rest = disc_rows
                first["diversityPhase"] = "first_presentation_of_wholly_unseen_nfdisc"
                unseen_first.append(first)
                for row in rest:
                    row["diversityPhase"] = "additional_presentation_same_nfdisc"
                    remainder.append(row)
            else:
                for row in disc_rows:
                    row["diversityPhase"] = "additional_presentation_previously_tested_nfdisc"
                    remainder.append(row)

    key = lambda row: (
        -float(row["compatibleClassSizeFraction"]),
        int(row["sourceR"]),
        int(row["sourceFieldDiscAbs"]),
        int(row["sourceCoefficientBytes"]),
        row["submissionId"],
        int(row["polynomialIndex"]),
    )
    ordered = [*sorted(unseen_first, key=key), *sorted(remainder, key=key)]
    for rank, row in enumerate(ordered, start=1):
        row["stableExactGateRank"] = rank
    stage2.shared.write_jsonl_atomic(
        OUTPUT, ordered, sort_key=lambda row: int(row["stableExactGateRank"])
    )
    summary = {
        "frontierRows": len(ordered),
        "whollyUnseenNfdiscFirstRows": len(unseen_first),
        "sourceRCounts": {
            str(source_r): len(rows_by_r[source_r]) for source_r in sorted(rows_by_r)
        },
        "compatibleClassProfiles": compatible,
        "testedSourceKeysExcluded": len(tested),
        "output": str(OUTPUT),
        "outputSha256": stage2.sha256(OUTPUT),
    }
    stage2.shared.write_json_atomic(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
