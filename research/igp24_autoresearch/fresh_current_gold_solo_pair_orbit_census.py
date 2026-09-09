#!/usr/bin/env python3
"""Current offline tc0/tc1 frontier from sealed pair actions and cached profiles."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import analyze_rank12_routes as shared
import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import fresh_t00134_pair_orbit_census as sole


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
FRONTIER = DATA / "fresh_current_gold_solo_pair_orbit_frontier_20260730.jsonl"
TESTED = DATA / "fresh_current_gold_solo_pair_orbit_tested_20260730.jsonl"
READY = DATA / "fresh_current_gold_solo_pair_orbit_ready_exact_20260730.jsonl"
SUMMARY = DATA / "fresh_current_gold_solo_pair_orbit_summary_20260730.json"


def public_pair(pair: tuple[str, int]) -> dict:
    return {"label": pair[0], "r": pair[1]}


def exact_classes(
    source_pair: tuple[str, int],
    action: dict,
    profiles: dict[str, dict[tuple[int, int], dict]],
) -> tuple[list[dict], str]:
    source_label, source_r = source_pair
    action_targets = {
        int(row["orbitIndex"]): str(row["targetLabel"])
        for row in action.get("targets") or []
    }
    if source_r == 24:
        return [
            {
                "classIndex": -1,
                "classSize": 1,
                "sourceR": 24,
                "targets": {
                    orbit_index: (label, 24)
                    for orbit_index, label in action_targets.items()
                },
            }
        ], "identity_complex_conjugation"
    compatible = [
        row
        for (r, _class_index), row in profiles.get(source_label, {}).items()
        if int(r) == source_r
    ]
    if compatible and any(
        set(row["targets"]) != set(action_targets) for row in compatible
    ):
        raise ValueError(f"incomplete exact profile for {source_label}/r{source_r}")
    return compatible, "exact_cached_conjugacy_profiles"


def route_for_source(
    source_pair: tuple[str, int],
    action: dict,
    classes: list[dict],
    profile_kind: str,
    target_states: dict[tuple[str, int], dict],
) -> dict | None:
    eligible = set(target_states)
    gold = {pair for pair, row in target_states.items() if row["teamCount"] == 0}
    solo = eligible - gold
    class_gold: list[set[tuple[str, int]]] = []
    class_solo: list[set[tuple[str, int]]] = []
    class_all: list[set[tuple[str, int]]] = []
    evidence: dict[tuple[str, int], dict[int, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for class_row in classes:
        gold_here: set[tuple[str, int]] = set()
        solo_here: set[tuple[str, int]] = set()
        for orbit_index, raw_pair in class_row["targets"].items():
            pair = (str(raw_pair[0]), int(raw_pair[1]))
            if pair not in eligible:
                continue
            (gold_here if pair in gold else solo_here).add(pair)
            evidence[pair][int(orbit_index)].append(
                int(class_row["classIndex"])
            )
        class_gold.append(gold_here)
        class_solo.append(solo_here)
        class_all.append(gold_here | solo_here)
    union_gold = set().union(*class_gold) if class_gold else set()
    union_solo = set().union(*class_solo) if class_solo else set()
    if not union_gold and not union_solo:
        return None
    guaranteed_gold_pairs = (
        set.intersection(*class_gold) if class_gold else set()
    )
    guaranteed_solo_pairs = (
        set.intersection(*class_solo) if class_solo else set()
    )
    guaranteed_pairs = set.intersection(*class_all) if class_all else set()
    mass = sum(int(row["classSize"]) for row in classes)
    expected_gold = sum(
        int(row["classSize"]) * len(pairs)
        for row, pairs in zip(classes, class_gold)
    ) / mass
    expected_solo = sum(
        int(row["classSize"]) * len(pairs)
        for row, pairs in zip(classes, class_solo)
    ) / mass
    any_gold = sum(
        int(row["classSize"])
        for row, pairs in zip(classes, class_gold)
        if pairs
    ) / mass
    any_eligible = sum(
        int(row["classSize"])
        for row, pairs in zip(classes, class_all)
        if pairs
    ) / mass
    target_counts = Counter(
        str(row["targetLabel"]) for row in action.get("targets") or []
    )
    return {
        "sourceLabel": source_pair[0],
        "sourceT": int(action["sourceT"]),
        "sourceR": source_pair[1],
        "length24OrbitCount": int(action["length24OrbitCount"]),
        "allLength24ActionsFaithful": all(
            int(row["kernelOrder"]) == 1 for row in action.get("targets") or []
        ),
        "targetLabelMultiplicity": dict(sorted(target_counts.items())),
        "hasRepeatedTargetLabel": any(value > 1 for value in target_counts.values()),
        "signatureProfileKind": profile_kind,
        "compatibleClassIndexes": sorted(
            int(row["classIndex"]) for row in classes
        ),
        "compatibleClassWeight": mass,
        "reachableGoldPairs": [
            public_pair(pair) for pair in sorted(union_gold)
        ],
        "reachableSoloPairs": [
            public_pair(pair) for pair in sorted(union_solo)
        ],
        "guaranteedGoldPairs": [
            public_pair(pair) for pair in sorted(guaranteed_gold_pairs)
        ],
        "guaranteedSoloPairs": [
            public_pair(pair) for pair in sorted(guaranteed_solo_pairs)
        ],
        "guaranteedEligiblePairs": [
            public_pair(pair) for pair in sorted(guaranteed_pairs)
        ],
        "reachableGoldPairCount": len(union_gold),
        "reachableSoloPairCount": len(union_solo),
        "guaranteedGoldPairCount": len(guaranteed_gold_pairs),
        "guaranteedSoloPairCount": len(guaranteed_solo_pairs),
        "guaranteedEligiblePairCount": len(guaranteed_pairs),
        "minimumGoldPairsPerCompatibleClass": min(map(len, class_gold)),
        "minimumEligiblePairsPerCompatibleClass": min(map(len, class_all)),
        "expectedGoldFactorsByClassWeight": expected_gold,
        "expectedSoloFactorsByClassWeight": expected_solo,
        "expectedMarginalScoreUpperBound": expected_gold + 0.5 * expected_solo,
        "estimatedAnyGoldHitProbability": any_gold,
        "estimatedAnyEligibleHitProbability": any_eligible,
        "orbitEvidence": {
            f"{pair[0]}/r{pair[1]}": {
                str(index): sorted(class_indexes)
                for index, class_indexes in sorted(by_orbit.items())
            }
            for pair, by_orbit in sorted(evidence.items())
        },
    }


def rank_key(row: dict) -> tuple:
    has_gold = int(row["reachableGoldPairCount"]) > 0
    return (
        -int(has_gold),
        -int(row["minimumGoldPairsPerCompatibleClass"] > 0),
        -float(row["estimatedAnyGoldHitProbability"]),
        -float(row["expectedGoldFactorsByClassWeight"]),
        -int(row["minimumEligiblePairsPerCompatibleClass"] > 0),
        -float(row["expectedMarginalScoreUpperBound"]),
        int(row["length24OrbitCount"]) != 1,
        int(row["sourceCoefficientBytes"]),
        int(row["sourceT"]),
        int(row["sourceR"]),
        str(row["sourceCoefficientSha256"]),
    )


def main() -> int:
    actions, action_provenance, action_artifacts = (
        pair_routes.load_sealed_actions()
    )
    profiles, profile_provenance, profile_artifacts = pair_routes.load_profiles(
        actions, action_artifacts
    )
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        snapshot = pair_routes.load_ledger_snapshot(connection)
        candidate_snapshot = pair_routes.exact_candidate_and_receipt_snapshot(
            connection
        )
        supplemental, supplemental_artifacts = (
            outbox_audit.supplemental_exact_pair_index()
        )
        pair_index = outbox_audit.merged_pair_index(
            candidate_snapshot, supplemental
        )
        outbox_hashes, outbox_pairs, outbox_meta = (
            outbox_audit.outbox_exclusions(pair_index, connection)
        )
        accepted = sole.accepted_presentations(connection)

    receipt_pairs = candidate_snapshot["receiptPairs"]
    base_low = {
        pair
        for pair, state in snapshot["targets"].items()
        if state["teamCount"] in (0, 1)
    }
    eligible = base_low - snapshot["baseline"] - snapshot["owned"]
    eligible -= receipt_pairs | outbox_pairs
    target_states = {pair: snapshot["targets"][pair] for pair in eligible}
    gold_pairs = {
        pair for pair, state in target_states.items() if state["teamCount"] == 0
    }
    solo_pairs = set(target_states) - gold_pairs

    presentation_hash = {
        (str(row["submissionId"]), int(row["polynomialIndex"])): str(
            row["sourceCoefficientSha256"]
        )
        for source_rows in accepted.values()
        for row in source_rows
    }
    tested_hashes, tested_evidence = sole.tested_source_hashes(
        presentation_hash
    )

    frontier = []
    tested = []
    resolved_pairs = 0
    unresolved_pairs = 0
    structural_source_pairs = 0
    for source_pair, source_rows in sorted(
        accepted.items(), key=lambda item: pair_routes.pair_sort_key(item[0])
    ):
        action = actions.get(source_pair[0])
        if action is None or int(action.get("length24OrbitCount", 0)) < 1:
            continue
        structural_source_pairs += 1
        classes, profile_kind = exact_classes(source_pair, action, profiles)
        if not classes:
            unresolved_pairs += 1
            continue
        resolved_pairs += 1
        structural = route_for_source(
            source_pair, action, classes, profile_kind, target_states
        )
        if structural is None:
            continue
        structural["actionArtifact"] = action_provenance[source_pair[0]]
        structural["profileArtifacts"] = sorted(
            profile_provenance.get(source_pair, set())
        )
        for source in sole.canonical_presentations(source_rows):
            digest = str(source["sourceCoefficientSha256"])
            row = {
                **source,
                **structural,
                "alreadyPairTested": digest in tested_hashes,
                "testedEvidence": tested_evidence.get(digest, []),
            }
            (tested if digest in tested_hashes else frontier).append(row)

    frontier.sort(key=rank_key)
    tested.sort(key=rank_key)
    for rank, row in enumerate(frontier, start=1):
        row["priorityRank"] = rank
    shared.write_jsonl_atomic(
        FRONTIER, frontier, sort_key=lambda row: int(row["priorityRank"])
    )
    shared.write_jsonl_atomic(
        TESTED,
        tested,
        sort_key=lambda row: (
            int(row["sourceT"]),
            int(row["sourceR"]),
            str(row["sourceCoefficientSha256"]),
        ),
    )

    ready_by_hash = {}
    for candidate in candidate_snapshot["candidates"]:
        digest = str(candidate.get("coefficientSha256"))
        pair = (
            str(candidate.get("targetLabel")),
            int(candidate.get("targetR", -1)),
        )
        if (
            pair not in eligible
            or digest in candidate_snapshot["knownCandidateHashes"]
            or digest in candidate_snapshot["receiptHashes"]
            or digest in outbox_hashes
            or digest in candidate_snapshot["ambiguousHashes"]
        ):
            continue
        ready_by_hash[digest] = {
            "coefficientSha256": digest,
            "targetPair": public_pair(pair),
            "targetTeamCount": snapshot["targets"][pair]["teamCount"],
            "fieldDiscriminantAbs": (
                str(candidate["fieldDiscriminantAbs"])
                if candidate.get("fieldDiscriminantAbs") is not None
                else None
            ),
            "polynomialDiscriminantAbs": (
                str(candidate["polynomialDiscriminantAbs"])
                if candidate.get("polynomialDiscriminantAbs") is not None
                else None
            ),
            "proof": candidate.get("proof"),
            "sourcePins": candidate.get("sourcePins"),
            "coefficientMaterialIncluded": False,
        }
    ready = sorted(
        ready_by_hash.values(),
        key=lambda row: (
            int(row["targetTeamCount"]),
            int(row["fieldDiscriminantAbs"] or 10**10000),
            str(row["coefficientSha256"]),
        ),
    )
    shared.write_jsonl_atomic(
        READY,
        ready,
        sort_key=lambda row: (
            int(row["targetTeamCount"]),
            int(row["fieldDiscriminantAbs"] or 10**10000),
            str(row["coefficientSha256"]),
        ),
    )

    summary = {
        "schemaVersion": "fresh-current-gold-solo-pair-orbit-census-v1",
        "status": "current_offline_cached_profile_frontier",
        "targetBoundary": {
            "cachePairs": len(snapshot["targets"]),
            "cacheGeneratedAtMaximum": max(
                state["generatedAt"] for state in snapshot["targets"].values()
            ),
            "tc0Tc1BeforeExclusions": len(base_low),
            "eligibleAfterBaselineOwnedReceiptOutbox": len(eligible),
            "eligibleGoldPairs": len(gold_pairs),
            "eligibleSoloPairs": len(solo_pairs),
            "baselinePairsExcluded": len(base_low & snapshot["baseline"]),
            "ownedPairsExcluded": len(base_low & snapshot["owned"]),
            "receiptPairsExcluded": len(base_low & receipt_pairs),
            "outboxPairsExcluded": len(base_low & outbox_pairs),
        },
        "coverage": {
            "sealedActionLabels": len(actions),
            "cachedProfileLabels": len(profiles),
            "acceptedStructuralSourcePairs": structural_source_pairs,
            "exactSignatureResolvedSourcePairs": resolved_pairs,
            "signatureUnresolvedSourcePairs": unresolved_pairs,
            "untestedSourcePresentationsWithReachableLowContention": len(frontier),
            "testedSourcePresentationsWithReachableLowContention": len(tested),
            "readyExactNovelCandidates": len(ready),
            "untestedRoutesWithGoldReachability": sum(
                int(row["reachableGoldPairCount"]) > 0 for row in frontier
            ),
            "untestedRoutesGuaranteedSomeGold": sum(
                int(row["minimumGoldPairsPerCompatibleClass"]) > 0
                for row in frontier
            ),
            "untestedRoutesGuaranteedSomeEligible": sum(
                int(row["minimumEligiblePairsPerCompatibleClass"]) > 0
                for row in frontier
            ),
        },
        "artifacts": {
            "actionMaps": action_artifacts,
            "profileCaches": profile_artifacts,
            "supplementalPairMaps": supplemental_artifacts,
            "frontier": {
                "path": str(FRONTIER.relative_to(ROOT)),
                "sha256": shared.sha256_file(FRONTIER),
            },
            "tested": {
                "path": str(TESTED.relative_to(ROOT)),
                "sha256": shared.sha256_file(TESTED),
            },
            "readyExact": {
                "path": str(READY.relative_to(ROOT)),
                "sha256": shared.sha256_file(READY),
            },
        },
        "exclusionAudit": {
            "receipt": candidate_snapshot["receiptAudit"],
            "outbox": outbox_meta,
        },
        "ranking": (
            "gold reachability first; guaranteed gold; gold hit probability; "
            "expected gold factors; guaranteed eligible; expected 1/0.5 score"
        ),
        "coefficientMaterialIncluded": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    shared.write_json_atomic(SUMMARY, summary)
    print(
        json.dumps(
            {
                "summary": str(SUMMARY.relative_to(ROOT)),
                "targetBoundary": summary["targetBoundary"],
                "coverage": summary["coverage"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
