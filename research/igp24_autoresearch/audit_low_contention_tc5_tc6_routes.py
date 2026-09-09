#!/usr/bin/env python3
"""Light-only audit for deterministic tc5 and tc6 pair frontiers.

This is a scouting/audit step only.  It reads the sealed unordered-pair
actions, cached exact conjugacy profiles, the current ledger, exact candidate
lineage, and local receipts.  It performs no polynomial arithmetic, network
operation, ledger write, or submission.

The resulting runbooks are fail-closed handoffs.  They remain executable only
while their pinned ledger/target/receipt boundary is current; execution after
the tc4 wave must first repeat this audit against the then-current boundary.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_higher_tc_routes as higher
import audit_low_contention_pair_routes as base
import run_low_contention_sequential as lane


ROOT = lane.ROOT
DATA = lane.DATA
TC4_CERTIFICATE = DATA / "low_contention_tc4_routes_certificate.json"
CERTIFICATE = DATA / "low_contention_tc5_tc6_routes_certificate.json"
SUMMARY = DATA / "low_contention_tc5_tc6_routes_summary.json"
TEAM_COUNTS = (5, 6)


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def reliability_rank(route: dict) -> tuple[int, int]:
    """Prefer the simplest exact proof path; every selected route is exact."""
    proof_rank = (
        0 if route["proofKind"] == "identity_complex_conjugation" else 1
    )
    return proof_rank, int(route["compatibleClassCount"])


def route_rank(route: dict, snapshot: dict, team_count: int) -> tuple:
    target_pair = next(iter(route["lowOutcomes"]))
    state = snapshot["targets"][target_pair]
    return (
        int(team_count),
        *reliability_rank(route),
        int(route["anchor"]["coefficientBytes"]),
        int(state["minimumDiscAbs"] or 10**1000),
        *base.pair_sort_key(target_pair),
        *base.pair_sort_key(route["sourcePair"]),
        int(route["orbitIndex"]),
    )


def rerank_census(census: dict, snapshot: dict) -> dict:
    """Deduplicate targets and apply upside/reliability-first ordering."""
    team_count = int(census["teamCount"])
    best_by_target: dict[tuple[str, int], dict] = {}
    for route in sorted(
        census["executableRoutes"],
        key=lambda row: route_rank(row, snapshot, team_count),
    ):
        target_pair = next(iter(route["lowOutcomes"]))
        best_by_target.setdefault(target_pair, route)
    return {
        **census,
        "selectedRoutes": sorted(
            best_by_target.values(),
            key=lambda row: route_rank(row, snapshot, team_count),
        ),
    }


def make_runbooks(censuses: list[dict], snapshot: dict) -> list[dict]:
    runbooks = []
    priority = 0
    for census in censuses:
        team_count = int(census["teamCount"])
        for layer_index, route in enumerate(
            census["selectedRoutes"], start=1
        ):
            priority += 1
            source_pair = route["sourcePair"]
            target_pair = next(iter(route["lowOutcomes"]))
            anchor = route["anchor"]
            route_id = (
                f"hc{team_count}_{layer_index:03d}_"
                f"{source_pair[0]}_r{source_pair[1]}_to_"
                f"{target_pair[0]}_r{target_pair[1]}"
            )
            output = DATA / f"low_contention_{route_id}_result.jsonl"
            temporary = output.with_suffix(output.suffix + ".tmp")
            if output.exists() or temporary.exists():
                raise lane.GuardFailure(
                    f"tc{team_count} output already exists: {output}"
                )
            action_path = ROOT / route["actionArtifact"]["path"]
            state = snapshot["targets"][target_pair]
            proof_kind = route["proofKind"]
            reliability = (
                "tier_a_identity_exact_single_orbit"
                if proof_kind == "identity_complex_conjugation"
                else "tier_a_exact_profile_deterministic_single_orbit"
            )
            runbooks.append(
                {
                    "priorityRank": priority,
                    "layerRank": layer_index,
                    "routeId": route_id,
                    "status": "audited_waiting_for_post_tc4_boundary_recheck",
                    "source": {
                        "submissionId": anchor["submissionId"],
                        "polynomialIndex": anchor["polynomialIndex"],
                        "label": source_pair[0],
                        "r": source_pair[1],
                        "coefficientSha256": anchor["coefficientSha256"],
                        "coefficientBytes": anchor["coefficientBytes"],
                        "ledgerStatus": "accepted_scoreable",
                        "notPreviouslyPairConstructed": True,
                    },
                    "target": {
                        "label": target_pair[0],
                        "r": target_pair[1],
                        "teamCountAtSeal": team_count,
                        "discoveredAtSeal": state["discovered"],
                        "minimumDiscAbsAtSeal": state["minimumDiscAbs"],
                        "generatedAtSeal": state["generatedAt"],
                        "projectedMarginalScoreExact": base.fraction_text(
                            Fraction(1, 2**team_count)
                        ),
                        "projectionQualifier": (
                            "upper_bound_before_discriminant_penalty"
                        ),
                    },
                    "routeReliability": {
                        "tier": reliability,
                        "proofKind": proof_kind,
                        "compatibleClassCount": route[
                            "compatibleClassCount"
                        ],
                        "exactDeterministic": True,
                        "singleLength24Orbit": True,
                    },
                    "exactAction": {
                        "orbitIndex": route["orbitIndex"],
                        "length24OrbitCount": 1,
                        "deterministicAcrossCompatibleClasses": True,
                        "compatibleClassCount": route[
                            "compatibleClassCount"
                        ],
                        "proofKind": proof_kind,
                        "actionArtifact": route["actionArtifact"],
                        "profileArtifacts": route["profileArtifacts"],
                    },
                    "guards": {
                        "outputAbsentAtSeal": True,
                        "outputTemporaryAbsentAtSeal": True,
                        "targetTeamCountExactAtSeal": True,
                        "targetNonbaselineAtSeal": (
                            target_pair not in snapshot["baseline"]
                        ),
                        "targetUnownedAtSeal": (
                            target_pair not in snapshot["owned"]
                        ),
                        "targetNotKnownVerificationPairAtSeal": (
                            target_pair not in snapshot["knownPairs"]
                        ),
                        "targetNotReceiptPairAtSeal": True,
                        "sourceAcceptedScoreableAtSeal": True,
                        "sourceAnchorFreshUntestedAtSeal": True,
                        "sourceHashPinned": True,
                        "postTc4FullAuditRerunRequired": True,
                        "postRunMustRecheckTargetReceiptAndOutputHash": True,
                        "submissionAuthorized": False,
                    },
                    "output": str(output.relative_to(ROOT)),
                    "heavyCommand": [
                        "/usr/local/bin/sage",
                        "-python",
                        "pair_sum_one.sage.py",
                        anchor["submissionId"],
                        str(anchor["polynomialIndex"]),
                        "--orbit-map",
                        str(action_path.relative_to(ROOT)),
                        "--expected-source-hash",
                        anchor["coefficientSha256"],
                        "--expected-target",
                        target_pair[0],
                        "--output-jsonl",
                        str(output.relative_to(ROOT)),
                    ],
                    "postflightCommand": [
                        "python3",
                        "validate_low_contention_pair_route.py",
                        "--certificate",
                        str(CERTIFICATE.relative_to(ROOT)),
                        "--route-id",
                        route_id,
                        "--result",
                        str(output.relative_to(ROOT)),
                    ],
                }
            )
    return runbooks


def census_summary(census: dict) -> dict:
    executable_targets = {
        next(iter(route["lowOutcomes"]))
        for route in census["executableRoutes"]
    }
    proof_counts: dict[str, int] = {}
    for route in census["selectedRoutes"]:
        kind = str(route["proofKind"])
        proof_counts[kind] = proof_counts.get(kind, 0) + 1
    return {
        "teamCount": census["teamCount"],
        "deterministicRoutes": len(census["deterministicRoutes"]),
        "deterministicSingleOrbitRoutes": len(census["singleOrbitRoutes"]),
        "deterministicSingleOrbitRoutesWithFreshAnchor": len(
            census["executableRoutes"]
        ),
        "distinctExecutableTargets": len(executable_targets),
        "selectedRunbooks": len(census["selectedRoutes"]),
        "selectedProofKindCounts": dict(sorted(proof_counts.items())),
        "testedSourceKeys": len(census["lineage"]["testedSourceKeys"]),
        "testedSourceHashes": len(
            census["lineage"]["testedSourceHashes"]
        ),
        "cachedExactMissClosures": census["lineage"][
            "closedAnchorCount"
        ],
        "cachedNovelReadyCandidates": len(
            census["lineage"]["readyNovelLowCandidates"]
        ),
    }


def main() -> int:
    if CERTIFICATE.exists() or SUMMARY.exists():
        raise FileExistsError("refusing to overwrite tc5/tc6 route audit")

    tc4_certificate = lane.read_json(TC4_CERTIFICATE)
    if (
        tc4_certificate.get("status")
        != "certified_light_only_runbooks_ready"
        or not all((tc4_certificate.get("checks") or {}).values())
    ):
        raise lane.GuardFailure("tc4 frontier audit is not fully certified")

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
        candidate_snapshot = base.exact_candidate_and_receipt_snapshot(
            connection
        )
        censuses = []
        for team_count in TEAM_COUNTS:
            census = higher.census_for_count(
                team_count,
                snapshot,
                actions,
                action_provenance,
                profiles,
                profile_provenance,
                candidate_snapshot,
                connection,
            )
            censuses.append(rerank_census(census, snapshot))
    finally:
        connection.close()

    runbooks = make_runbooks(censuses, snapshot)
    target_pairs = [
        (row["target"]["label"], int(row["target"]["r"]))
        for row in runbooks
    ]
    if len(target_pairs) != len(set(target_pairs)):
        raise lane.GuardFailure("tc5/tc6 runbook targets are not distinct")

    receipt_audit = candidate_snapshot["receiptAudit"]
    target_rows = [
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
    accepted_rows = [
        [label, r]
        for label, r in sorted(snapshot["owned"], key=base.pair_sort_key)
    ]
    layer_counts = {
        str(census["teamCount"]): len(census["selectedRoutes"])
        for census in censuses
    }
    total_score = sum(
        (
            Fraction(len(census["selectedRoutes"]), 2 ** int(census["teamCount"]))
            for census in censuses
        ),
        Fraction(0),
    )
    all_non_submission_guards = [
        value
        for row in runbooks
        for key, value in row["guards"].items()
        if key != "submissionAuthorized"
    ]
    certificate = {
        "schemaVersion": "low-contention-unordered-pair-route-audit-v2",
        "scope": "deterministic_tc5_tc6_fresh_untested_anchors",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_light_only_runbooks_audited_post_tc4_recheck_required"
            if runbooks
            else "certified_light_only_no_executable_runbooks"
        ),
        "method": (
            "sealed exact pair actions/profiles + current accepted ledger + "
            "exact candidate/receipt lineage + current tc5/tc6 targets"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "tc4Audit": lane.artifact(TC4_CERTIFICATE),
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
            "candidateCorpus": candidate_snapshot["corpus"],
            "database": {"path": str(lane.DB.relative_to(ROOT))},
            "receiptsDirectory": str(lane.RECEIPTS.relative_to(ROOT)),
            "auditProgram": lane.artifact(Path(__file__).resolve()),
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
        "censuses": {
            str(census["teamCount"]): census_summary(census)
            for census in censuses
        },
        "ranking": {
            "order": [
                "larger projected marginal score: tc5 before tc6",
                "identity complex-conjugation proof before exact cached profiles",
                "fewer compatible exact conjugacy classes",
                "shorter accepted source coefficient encoding",
                "smaller current target minimum discriminant",
                "target/source/orbit deterministic tie-break",
            ],
            "reliabilityQualifier": (
                "all selected routes exact, deterministic across compatible "
                "classes, and single length-24 orbit"
            ),
            "scoreFormula": (
                "1/2^teamCount per target, upper bound before "
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
        "executionBoundary": {
            "tc4NotYetReceiptSealedAtAudit": True,
            "fullAuditRerunAfterTc4Required": True,
            "reason": (
                "tc4 execution can consume source anchors, add receipt pairs, "
                "and change live target team counts"
            ),
        },
        "checks": {
            "tc4LightAuditFullyCertified": True,
            "cachedNovelTc5Tc6CandidatesAbsent": all(
                not census["lineage"]["readyNovelLowCandidates"]
                for census in censuses
            ),
            "allRunbooksDeterministicSingleOrbitTc5OrTc6": all(
                row["target"]["teamCountAtSeal"] in TEAM_COUNTS
                and row["exactAction"]["length24OrbitCount"] == 1
                and row["exactAction"][
                    "deterministicAcrossCompatibleClasses"
                ]
                for row in runbooks
            ),
            "allRunbookAnchorsAcceptedScoreableFreshUntested": all(
                row["source"]["notPreviouslyPairConstructed"]
                and row["guards"]["sourceAcceptedScoreableAtSeal"]
                and row["guards"]["sourceAnchorFreshUntestedAtSeal"]
                for row in runbooks
            ),
            "allTargetsDistinct": len(target_pairs) == len(set(target_pairs)),
            "allTargetsNonbaselineUnownedUnknownNonreceipt": all(
                all_non_submission_guards
            ),
            "allOutputsAbsent": all(
                row["guards"]["outputAbsentAtSeal"]
                and row["guards"]["outputTemporaryAbsentAtSeal"]
                for row in runbooks
            ),
            "tc5RankedAheadOfTc6": all(
                runbooks[index]["target"]["teamCountAtSeal"]
                <= runbooks[index + 1]["target"]["teamCountAtSeal"]
                for index in range(len(runbooks) - 1)
            ),
            "postTc4RerunGuardPresent": all(
                row["guards"]["postTc4FullAuditRerunRequired"]
                for row in runbooks
            ),
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
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if base.COEFFICIENT_LINE_RE.search(rendered):
        raise lane.GuardFailure(
            "coefficient payload entered tc5/tc6 audit certificate"
        )
    lane.exclusive_text(CERTIFICATE, rendered)

    best = runbooks[0] if runbooks else None
    summary = {
        "schemaVersion": "low-contention-tc5-tc6-summary-v1",
        "status": certificate["status"],
        "certificate": lane.artifact(CERTIFICATE),
        "censuses": certificate["censuses"],
        "runbooks": len(runbooks),
        "teamCountDistribution": layer_counts,
        "projectedMarginalScoreExact": base.fraction_text(total_score),
        "bestRouteId": best["routeId"] if best else None,
        "bestRouteTeamCount": (
            best["target"]["teamCountAtSeal"] if best else None
        ),
        "bestRouteProjectedMarginalScoreExact": (
            best["target"]["projectedMarginalScoreExact"] if best else None
        ),
        "bestRouteReliabilityTier": (
            best["routeReliability"]["tier"] if best else None
        ),
        "postTc4FullAuditRerunRequired": True,
        "targetRows": len(snapshot["targets"]),
        "acceptedScoreablePairs": len(snapshot["owned"]),
        "receiptCount": receipt_audit["receiptCount"],
        "receiptPolynomialHashes": receipt_audit[
            "receiptPolynomialHashes"
        ],
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
