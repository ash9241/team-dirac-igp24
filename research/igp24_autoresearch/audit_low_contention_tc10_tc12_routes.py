#!/usr/bin/env python3
"""Seal the light-only deterministic tc10/tc11/tc12 volume frontier.

The audit reuses the sealed unordered-pair action/profile chain and the exact
single-orbit eligibility engine.  It joins those proofs to current accepted
anchors and targets, then excludes every intact receipt and every current text
outbox, including the queued v17 exact-gold batch.  It performs no polynomial
arithmetic, network operation, ledger write, submission, Sage run, or GAP run.

The resulting runbooks obey ``run_deterministic_frontier_coordinator.py``'s
generic finalized-route contract and remain fail-closed on boundary drift.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_higher_tc_routes as higher
import audit_low_contention_pair_routes as base
import audit_low_contention_tc5_tc6_routes as scout
import audit_low_contention_tc7_tc9_routes as exclusions
import run_low_contention_sequential as lane


ROOT = lane.ROOT
DATA = lane.DATA
TEAM_COUNTS = (10, 11, 12)
CERTIFICATE = DATA / "low_contention_tc10_tc12_routes_certificate.json"
SUMMARY = DATA / "low_contention_tc10_tc12_routes_summary.json"
OUTBOX_INDEX = DATA / "low_contention_tc10_tc12_outbox_exclusion_index.json"
V17_RECEIPT_ID = "sub_7f6cd8ec78604eaca6fa5a8e373f90e5"
V17_QUEUED_GOLD_PAIRS = {
    ("24T11827", 16),
    ("24T19066", 16),
}
TC7_RECEIPT_ID = "sub_f34764affb9f428b93cb5f3577001a73"
TC7_QUEUED_PAIRS = {
    ("24T17117", 24),
    ("24T7848", 24),
    ("24T8190", 24),
    ("24T8192", 24),
    ("24T17187", 4),
    ("24T12798", 20),
    ("24T10390", 20),
    ("24T7949", 12),
    ("24T19033", 12),
}
REQUIRED_RECEIPTS = {
    **exclusions.NAMED_RECEIPTS,
    V17_RECEIPT_ID: 2,
    TC7_RECEIPT_ID: 9,
}


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def layer_summary(census: dict) -> dict:
    team_count = int(census["teamCount"])
    selected = census["selectedRoutes"]
    coverage = census["coverage"]
    return {
        **exclusions.census_summary(census),
        "eligibleFreshTargetsBeforeReceiptExclusion": coverage[
            "eligibleTc1Tc2PairsBeforeReceiptExclusion"
        ],
        "eligibleFreshTargetsAfterAllPairExclusions": coverage[
            "eligibleTc1Tc2PairsAfterAllPairExclusions"
        ],
        "receiptPairCollisionsAtLayer": coverage[
            "receiptPairCollisionsAtTargetLayer"
        ],
        "projectedMarginalScoreExact": base.fraction_text(
            Fraction(len(selected), 2**team_count)
        ),
        "projectionQualifier": "upper_bound_before_discriminant_penalty",
    }


def boundary_rows(snapshot: dict) -> tuple[list[list], list[list]]:
    accepted = [
        [label, r]
        for label, r in sorted(snapshot["owned"], key=base.pair_sort_key)
    ]
    targets = [
        [
            label,
            r,
            state["teamCount"],
            state["discovered"],
            state["minimumDiscAbs"],
            state["generatedAt"],
        ]
        for (label, r), state in sorted(
            snapshot["targets"].items(),
            key=lambda item: base.pair_sort_key(item[0]),
        )
    ]
    return accepted, targets


def filesystem_boundary(directory: Path, pattern: str) -> list[list[str]]:
    return [
        [str(path.relative_to(ROOT)), lane.sha256_path(path)]
        for path in sorted(directory.glob(pattern))
        if path.is_file()
    ]


def revalidate_seal_boundary(
    sealed_boundary: dict,
    receipt_files: list[list[str]],
    outbox_files: list[list[str]],
) -> None:
    if filesystem_boundary(lane.RECEIPTS, "sub_*.json") != receipt_files:
        raise lane.GuardFailure("receipt filesystem changed during audit")
    if filesystem_boundary(ROOT / "outbox", "*.txt") != outbox_files:
        raise lane.GuardFailure("outbox filesystem changed during audit")
    connection = sqlite3.connect(
        f"file:{lane.DB.resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        current = base.load_ledger_snapshot(connection)
    finally:
        connection.close()
    accepted, targets = boundary_rows(current)
    actual = {
        "acceptedScoreablePairs": len(current["owned"]),
        "acceptedPairSetSha256": base.canonical_digest(accepted),
        "targetRows": len(current["targets"]),
        "targetSnapshotSha256": base.canonical_digest(targets),
        "targetGeneratedAtMin": min(
            state["generatedAt"] for state in current["targets"].values()
        ),
        "targetGeneratedAtMax": max(
            state["generatedAt"] for state in current["targets"].values()
        ),
    }
    if actual != sealed_boundary:
        raise lane.GuardFailure("ledger/target boundary changed during audit")


def validate_runbooks(
    runbooks: list[dict],
    snapshot: dict,
    receipt_pairs: set[tuple[str, int]],
    outbox_pairs: set[tuple[str, int]],
) -> None:
    target_pairs = [
        (str(row["target"]["label"]), int(row["target"]["r"]))
        for row in runbooks
    ]
    source_hashes = [
        str(row["source"]["coefficientSha256"]) for row in runbooks
    ]
    source_keys = [
        (
            str(row["source"]["submissionId"]),
            int(row["source"]["polynomialIndex"]),
        )
        for row in runbooks
    ]
    if (
        len(target_pairs) != len(set(target_pairs))
        or len(source_hashes) != len(set(source_hashes))
        or len(source_keys) != len(set(source_keys))
    ):
        raise lane.GuardFailure(
            "tc10/tc11/tc12 targets or exact source anchors are not distinct"
        )
    for index, (row, target_pair) in enumerate(
        zip(runbooks, target_pairs, strict=True), start=1
    ):
        state = snapshot["targets"].get(target_pair)
        if (
            int(row.get("priorityRank", -1)) != index
            or state is None
            or int(state["teamCount"]) not in TEAM_COUNTS
            or target_pair in snapshot["baseline"]
            or target_pair in snapshot["owned"]
            or target_pair in snapshot["knownPairs"]
            or target_pair in receipt_pairs
            or target_pair in outbox_pairs
            or int(row["exactAction"]["length24OrbitCount"]) != 1
            or row["exactAction"][
                "deterministicAcrossCompatibleClasses"
            ]
            is not True
            or row["routeReliability"]["exactDeterministic"] is not True
        ):
            raise lane.GuardFailure(
                f"runbook is not a fresh exact deterministic route: "
                f"{row.get('routeId')}"
            )


def main() -> int:
    for path in (CERTIFICATE, SUMMARY, OUTBOX_INDEX):
        if path.exists():
            raise FileExistsError(
                f"refusing to overwrite tc10/tc11/tc12 audit artifact: {path}"
            )

    receipt_files_at_start = filesystem_boundary(
        lane.RECEIPTS, "sub_*.json"
    )
    outbox_files_at_start = filesystem_boundary(ROOT / "outbox", "*.txt")
    actions, action_provenance, action_artifacts = base.load_sealed_actions()
    profiles, profile_provenance, profile_artifacts = base.load_profiles(
        actions, action_artifacts
    )
    connection = sqlite3.connect(
        f"file:{lane.DB.resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        snapshot = base.load_ledger_snapshot(connection)
        receipt_snapshot = base.exact_candidate_and_receipt_snapshot(
            connection
        )
        supplemental_index, supplemental_artifacts = (
            exclusions.supplemental_exact_pair_index()
        )
        pair_index = exclusions.merged_pair_index(
            receipt_snapshot, supplemental_index
        )
        named_receipt_audits, named_receipt_pairs = (
            exclusions.validate_named_receipts(
                receipt_snapshot,
                pair_index,
                REQUIRED_RECEIPTS,
            )
        )
        outbox_hashes, outbox_pairs, outbox_index = (
            exclusions.outbox_exclusions(pair_index, connection)
        )
        combined_receipt_pairs = (
            receipt_snapshot["receiptPairs"] | named_receipt_pairs
        )
        if not V17_QUEUED_GOLD_PAIRS <= (
            combined_receipt_pairs & outbox_pairs
        ):
            raise lane.GuardFailure(
                "queued v17 gold pairs are not jointly receipt/outbox excluded"
            )
        if not TC7_QUEUED_PAIRS <= (
            combined_receipt_pairs & outbox_pairs
        ):
            raise lane.GuardFailure(
                "queued tc7 pairs are not jointly receipt/outbox excluded"
            )
        exclusion_snapshot = {
            **receipt_snapshot,
            "receiptHashes": (
                receipt_snapshot["receiptHashes"] | outbox_hashes
            ),
            "receiptPairs": combined_receipt_pairs | outbox_pairs,
        }
        censuses = []
        for team_count in TEAM_COUNTS:
            census = higher.census_for_count(
                team_count,
                snapshot,
                actions,
                action_provenance,
                profiles,
                profile_provenance,
                exclusion_snapshot,
                connection,
            )
            censuses.append(scout.rerank_census(census, snapshot))
    finally:
        connection.close()

    runbooks = exclusions.finalize_runbooks(
        censuses, snapshot, certificate=CERTIFICATE
    )
    validate_runbooks(
        runbooks,
        snapshot,
        exclusion_snapshot["receiptPairs"],
        outbox_pairs,
    )
    if not runbooks:
        raise lane.GuardFailure(
            "tc10/tc11/tc12 deterministic volume frontier is unexpectedly empty"
        )

    layer_counts = {
        str(census["teamCount"]): len(census["selectedRoutes"])
        for census in censuses
    }
    total_score = sum(
        (
            Fraction(
                len(census["selectedRoutes"]),
                2 ** int(census["teamCount"]),
            )
            for census in censuses
        ),
        Fraction(0),
    )
    accepted_rows, target_rows = boundary_rows(snapshot)
    target_pairs = [
        (str(row["target"]["label"]), int(row["target"]["r"]))
        for row in runbooks
    ]
    source_hashes = [
        str(row["source"]["coefficientSha256"]) for row in runbooks
    ]
    source_keys = [
        (
            str(row["source"]["submissionId"]),
            int(row["source"]["polynomialIndex"]),
        )
        for row in runbooks
    ]
    receipt_audit = receipt_snapshot["receiptAudit"]
    best = runbooks[0]
    certificate = {
        "schemaVersion": "low-contention-deterministic-volume-frontier-v1",
        "scope": "finalized_deterministic_tc10_tc11_tc12_volume_frontier",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_final_receipt_outbox_aware_runbooks_ready",
        "method": (
            "sealed exact unordered-pair actions/profiles + exact current "
            "accepted anchors + current target cache + all intact receipts "
            "and current txt outboxes"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "boundary": {
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "acceptedPairSetSha256": base.canonical_digest(accepted_rows),
            "targetRows": len(snapshot["targets"]),
            "targetSnapshotSha256": base.canonical_digest(target_rows),
            "targetGeneratedAtMin": min(
                state["generatedAt"]
                for state in snapshot["targets"].values()
            ),
            "targetGeneratedAtMax": max(
                state["generatedAt"]
                for state in snapshot["targets"].values()
            ),
        },
        "artifacts": {
            "sealedActionMaps": action_artifacts,
            "profileArtifacts": profile_artifacts,
            "candidateCorpus": receipt_snapshot["corpus"],
            "supplementalExactPairMaps": supplemental_artifacts,
            "database": {"path": str(lane.DB.relative_to(ROOT))},
            "receiptsDirectory": str(lane.RECEIPTS.relative_to(ROOT)),
            "outboxExclusionIndex": {
                "path": str(OUTBOX_INDEX.relative_to(ROOT)),
                "sha256": None,
            },
            "auditProgram": lane.artifact(Path(__file__).resolve()),
        },
        "requiredReceiptAudit": named_receipt_audits,
        "queuedV17GoldExclusion": {
            "submissionId": V17_RECEIPT_ID,
            "submissionStatusAtAudit": next(
                row["submissionStatusAtAudit"]
                for row in named_receipt_audits
                if row["submissionId"] == V17_RECEIPT_ID
            ),
            "pairs": sorted(pair_text(pair) for pair in V17_QUEUED_GOLD_PAIRS),
            "receiptExcluded": True,
            "outboxExcluded": True,
        },
        "queuedTc7BatchExclusion": {
            "submissionId": TC7_RECEIPT_ID,
            "submissionStatusAtAudit": next(
                row["submissionStatusAtAudit"]
                for row in named_receipt_audits
                if row["submissionId"] == TC7_RECEIPT_ID
            ),
            "pairs": sorted(pair_text(pair) for pair in TC7_QUEUED_PAIRS),
            "receiptExcluded": True,
            "outboxExcluded": True,
        },
        "receiptExclusion": {
            "receiptCount": receipt_audit["receiptCount"],
            "receiptPolynomialHashes": receipt_audit[
                "receiptPolynomialHashes"
            ],
            "receiptTargetPairs": receipt_audit["receiptTargetPairs"],
            "queuedPossiblePairMapRows": receipt_audit[
                "queuedPossiblePairMapRows"
            ],
        },
        "outboxExclusion": {
            "outboxFiles": outbox_index["outboxFiles"],
            "nonemptyOutboxFiles": outbox_index["nonemptyOutboxFiles"],
            "canonicalPolynomialRows": outbox_index[
                "canonicalPolynomialRows"
            ],
            "distinctCoefficientHashes": len(outbox_hashes),
            "distinctPairsExcluded": len(outbox_pairs),
            "coefficientHashSetSha256": outbox_index[
                "coefficientHashSetSha256"
            ],
            "pairSetSha256": outbox_index["pairSetSha256"],
        },
        "combinedExclusion": {
            "distinctCoefficientHashes": len(
                exclusion_snapshot["receiptHashes"]
            ),
            "distinctPairs": len(exclusion_snapshot["receiptPairs"]),
        },
        "censuses": {
            str(census["teamCount"]): layer_summary(census)
            for census in censuses
        },
        "ranking": {
            "order": [
                "larger exact score: tc10 before tc11 before tc12",
                "identity complex-conjugation proof before exact cached profiles",
                "fewer compatible exact conjugacy classes",
                "shorter accepted source coefficient encoding",
                "smaller current target minimum discriminant",
                "target/source/orbit deterministic tie-break",
            ],
            "scoreFormula": (
                "1/2^teamCount per distinct target, upper bound before "
                "discriminant penalty"
            ),
        },
        "runbooks": runbooks,
        "runbookProjection": {
            "count": len(runbooks),
            "teamCountDistribution": layer_counts,
            "marginalScoreExact": base.fraction_text(total_score),
            "qualifier": "upper_bound_before_discriminant_penalty",
        },
        "bestRoute": {
            "routeId": best["routeId"],
            "sourcePair": pair_text(
                (best["source"]["label"], int(best["source"]["r"]))
            ),
            "targetPair": pair_text(
                (best["target"]["label"], int(best["target"]["r"]))
            ),
            "teamCount": int(best["target"]["teamCountAtSeal"]),
            "projectedMarginalScoreExact": best["target"][
                "projectedMarginalScoreExact"
            ],
            "reliabilityTier": best["routeReliability"]["tier"],
            "priorityRank": int(best["priorityRank"]),
        },
        "coordinatorCompatibility": {
            "program": "run_deterministic_frontier_coordinator.py",
            "finalizedRouteContract": True,
            "priorityRanksContiguous": True,
            "boundaryFailClosed": True,
            "suggestedBatchName": "low_contention_tc10_tc12",
            "submissionPathPresent": False,
        },
        "checks": {
            "sealedActionProfileChainExact": True,
            "allRequiredReceiptsPinnedIntactAndExcluded": (
                len(named_receipt_audits) == len(REQUIRED_RECEIPTS)
            ),
            "queuedV17GoldReceiptAndOutboxExcluded": True,
            "queuedTc7BatchReceiptAndOutboxExcluded": True,
            "allCurrentReceiptHashesAndPairsExcluded": True,
            "allCurrentTxtOutboxesCanonicalAndExcluded": all(
                outbox_index["checks"].values()
            ),
            "cachedNovelTc10Tc11Tc12CandidatesAbsent": all(
                not census["lineage"]["readyNovelLowCandidates"]
                for census in censuses
            ),
            "allRunbooksCurrentAcceptedFreshExactAnchors": all(
                row["guards"]["sourceAcceptedScoreableAtSeal"]
                and row["guards"]["sourceAnchorFreshUntestedAtSeal"]
                and row["guards"][
                    "sourceAnchorAcceptedAndHashRevalidatedAtSeal"
                ]
                for row in runbooks
            ),
            "allRunbooksDeterministicSingleOrbitExactSignature": all(
                row["target"]["teamCountAtSeal"] in TEAM_COUNTS
                and row["exactAction"]["length24OrbitCount"] == 1
                and row["exactAction"][
                    "deterministicAcrossCompatibleClasses"
                ]
                and row["routeReliability"]["exactDeterministic"]
                for row in runbooks
            ),
            "allSourceAnchorsDistinct": len(source_hashes)
            == len(set(source_hashes))
            == len(source_keys)
            == len(set(source_keys)),
            "allTargetPairsDistinctFreshNonbaselineUnowned": (
                len(target_pairs) == len(set(target_pairs))
                and all(
                    pair not in snapshot["baseline"]
                    and pair not in snapshot["owned"]
                    and pair not in snapshot["knownPairs"]
                    and pair not in exclusion_snapshot["receiptPairs"]
                    and pair not in outbox_pairs
                    for pair in target_pairs
                )
            ),
            "allOutputsAbsent": all(
                row["guards"]["outputAbsentAtSeal"]
                and row["guards"]["outputTemporaryAbsentAtSeal"]
                for row in runbooks
            ),
            "tc10RankedBeforeTc11BeforeTc12": all(
                runbooks[index]["target"]["teamCountAtSeal"]
                <= runbooks[index + 1]["target"]["teamCountAtSeal"]
                for index in range(len(runbooks) - 1)
            ),
            "genericCoordinatorRouteContractPresent": True,
            "certificateContainsNoCoefficientPayload": True,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "polynomialArithmeticRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }

    revalidate_seal_boundary(
        certificate["boundary"],
        receipt_files_at_start,
        outbox_files_at_start,
    )
    # Seal the outbox index first so its exact digest can be pinned by the
    # route certificate consumed by the generic coordinator.
    lane.exclusive_json(OUTBOX_INDEX, outbox_index)
    certificate["artifacts"]["outboxExclusionIndex"] = lane.artifact(
        OUTBOX_INDEX
    )
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if base.COEFFICIENT_LINE_RE.search(rendered):
        raise lane.GuardFailure(
            "coefficient payload entered tc10/tc11/tc12 certificate"
        )
    lane.exclusive_text(CERTIFICATE, rendered)

    summary = {
        "schemaVersion": "low-contention-tc10-tc12-summary-v1",
        "status": certificate["status"],
        "certificate": lane.artifact(CERTIFICATE),
        "censuses": certificate["censuses"],
        "runbooks": len(runbooks),
        "teamCountDistribution": layer_counts,
        "projectedMarginalScoreExact": base.fraction_text(total_score),
        "bestRoute": certificate["bestRoute"],
        "requiredReceiptsExcluded": len(named_receipt_audits),
        "queuedV17GoldExcluded": True,
        "queuedTc7BatchExcluded": True,
        "receiptCount": receipt_audit["receiptCount"],
        "receiptPolynomialHashes": receipt_audit[
            "receiptPolynomialHashes"
        ],
        "outboxFilesExcluded": outbox_index["outboxFiles"],
        "outboxPolynomialHashesExcluded": len(outbox_hashes),
        "acceptedScoreablePairs": len(snapshot["owned"]),
        "targetRows": len(snapshot["targets"]),
        "genericCoordinatorCompatible": True,
        "coefficientMaterialIncluded": False,
        "heavyWorkerLaunched": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    lane.exclusive_json(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        lane.GuardFailure,
        FileExistsError,
        ValueError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
