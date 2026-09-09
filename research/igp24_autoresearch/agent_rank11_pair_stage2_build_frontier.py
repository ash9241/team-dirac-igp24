#!/usr/bin/env python3
"""Build the exhaustive nonduplicate exact pair-action frontier for rank 11."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import agent_rank10_pair_stage2 as exact


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
TARGETS = DATA / "agent_fresh_rank11_current_sole_pairs.jsonl"
PROFILES = DATA / "agent_rank11_pair_stage2_profiles.jsonl"
OUTPUT = DATA / "agent_rank11_pair_stage2_frontier.jsonl"
SUMMARY = DATA / "agent_rank11_pair_stage2_frontier_summary.json"
TEAM_ID = "teamv2_26ddfb8c4e1e4193a4075695b88c5fb0"


def completed_source_identities() -> set[tuple[str, int]]:
    """Exclude every completed command identity, not only pair-named files."""
    completed = set()
    statuses = {"certified", "certified_multi", "certified_staged", "resolved"}
    for path in DATA.glob("*.jsonl"):
        if not path.is_file():
            continue
        for row in exact.read_jsonl(path):
            if str(row.get("status", "")) not in statuses:
                continue
            submission = row.get("sourceSubmissionId", row.get("submissionId"))
            index = row.get("sourcePolynomialIndex", row.get("polynomialIndex"))
            if submission is not None and index is not None:
                completed.add((str(submission), int(index)))
    return completed


def main() -> int:
    target_rows = exact.read_jsonl(TARGETS)
    if len(target_rows) != 247:
        raise RuntimeError(f"expected 247 current rank-11 pairs, got {len(target_rows)}")
    if any(str(row.get("teamId")) != TEAM_ID for row in target_rows):
        raise RuntimeError("target holder identity mismatch")
    target_pairs = {(str(row["label"]), int(row["r"])): row for row in target_rows}
    target_labels = {label for label, _r in target_pairs}

    orbit_rows = exact.read_jsonl(DATA / "pair_orbit_map.jsonl")
    orbit_by_label = {str(row["sourceLabel"]): row for row in orbit_rows}
    profiles = {str(row["sourceLabel"]): row for row in exact.read_jsonl(PROFILES)}
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        owned = exact.shared.source_rows(connection)
    owned_labels = {label for label, _r in owned}
    relevant = {
        label: row
        for label, row in orbit_by_label.items()
        if label in owned_labels
        and any(str(target["targetLabel"]) in target_labels for target in row["targets"])
    }
    if set(relevant) - set(profiles):
        raise RuntimeError("one or more relevant source profiles are missing")
    routes = exact.route_census(owned, relevant, profiles, target_pairs)
    tested = completed_source_identities()

    frontier = []
    excluded = []
    for route in routes:
        source_pair = (str(route["sourceLabel"]), int(route["sourceR"]))
        for source in owned[source_pair]:
            identity = (str(source["submissionId"]), int(source["polynomialIndex"]))
            row = {
                **route,
                "submissionId": identity[0],
                "polynomialIndex": identity[1],
                "sourceCoefficientSha256": str(source["coefficientSha256"]),
                "sourceCoefficientBytes": int(source["coefficientBytes"]),
                "sourceFieldDiscAbs": str(source["fieldDiscAbs"]),
            }
            if int(route["length24OrbitCount"]) == 1:
                row["targetLabel"] = str(relevant[source_pair[0]]["targets"][0]["targetLabel"])
            if identity in tested:
                excluded.append(row)
            else:
                frontier.append(row)

    tested_discs = {
        (str(row["sourceLabel"]), int(row["sourceR"]), str(row["sourceFieldDiscAbs"]))
        for row in excluded
    }
    grouped: dict[tuple[str, int, str], list[dict]] = defaultdict(list)
    for row in frontier:
        grouped[
            (str(row["sourceLabel"]), int(row["sourceR"]), str(row["sourceFieldDiscAbs"]))
        ].append(row)
    diverse, remainder = [], []
    for key, rows in grouped.items():
        rows.sort(
            key=lambda row: (
                int(row["sourceCoefficientBytes"]),
                str(row["submissionId"]),
                int(row["polynomialIndex"]),
            )
        )
        first, *rest = rows
        if key not in tested_discs:
            first["diversityPhase"] = "first_presentation_of_wholly_unseen_nfdisc"
        else:
            first["diversityPhase"] = "additional_presentation_previously_tested_nfdisc"
        diverse.append(first)
        for row in rest:
            row["diversityPhase"] = "additional_presentation_same_nfdisc"
            remainder.append(row)

    order_key = lambda row: (
        -int(row["guaranteedTargetPairCount"]),
        -float(row["expectedT00110FactorsByClassSize"]),
        int(row["length24OrbitCount"]) == 1,
        int(row["sourceT"]),
        int(row["sourceR"]),
        int(row["sourceFieldDiscAbs"]),
        int(row["sourceCoefficientBytes"]),
        str(row["submissionId"]),
        int(row["polynomialIndex"]),
    )
    ordered = [*sorted(diverse, key=order_key), *sorted(remainder, key=order_key)]
    for rank, row in enumerate(ordered, start=1):
        row["rank11ExactPacketRank"] = rank
    exact.shared.write_jsonl_atomic(
        OUTPUT, ordered, sort_key=lambda row: int(row["rank11ExactPacketRank"])
    )
    summary = {
        "rank11CurrentSolePairs": len(target_pairs),
        "relevantOwnedSourceLabels": len(relevant),
        "multiOrbitSourceLabels": sum(int(row["length24OrbitCount"]) > 1 for row in relevant.values()),
        "singleOrbitSourceLabels": sum(int(row["length24OrbitCount"]) == 1 for row in relevant.values()),
        "certifiedProfiles": len(profiles),
        "compatibleOwnedSourceSignatureRoutes": len(routes),
        "distinctReachableRank11Pairs": len({
            (pair["label"], int(pair["r"]))
            for route in routes for pair in route["reachableTargetPairs"]
        }),
        "completedCommandIdentitiesScanned": len(tested),
        "priorRoutePresentationsExcluded": len(excluded),
        "nonduplicatePackets": len(ordered),
        "nonduplicateDistinctSourceNfdiscs": len(grouped),
        "multiOrbitPackets": sum(int(row["length24OrbitCount"]) > 1 for row in ordered),
        "singleOrbitPackets": sum(int(row["length24OrbitCount"]) == 1 for row in ordered),
        "output": str(OUTPUT),
    }
    exact.shared.write_json_atomic(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
