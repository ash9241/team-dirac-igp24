#!/usr/bin/env python3
"""Audit deterministic tc4 pair routes and emit light-only guarded runbooks.

The audit reuses only sealed unordered-pair actions, cached exact conjugacy
profiles, the read-only ledger, exact candidate lineage, and receipts.  It
performs no polynomial arithmetic, network operation, or submission.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as base
import run_low_contention_sequential as lane


ROOT = lane.ROOT
DATA = lane.DATA
TC3_SEAL = DATA / "low_contention_tc3_frontier_receipt_mapping.json"
CERTIFICATE = DATA / "low_contention_tc4_routes_certificate.json"
SUMMARY = DATA / "low_contention_tc4_routes_summary.json"
TEAM_COUNT = 4


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def route_rank(route: dict, snapshot: dict) -> tuple:
    target_pair = next(iter(route["lowOutcomes"]))
    state = snapshot["targets"][target_pair]
    return (
        int(route["anchor"]["coefficientBytes"]),
        int(state["minimumDiscAbs"] or 10**1000),
        *base.pair_sort_key(target_pair),
        *base.pair_sort_key(route["sourcePair"]),
        int(route["orbitIndex"]),
    )


def transformed_snapshot(snapshot: dict, actions: dict) -> dict:
    """Map only the tc4 layer to the eligibility engine's tc2 sentinel."""
    targets = {
        pair: {
            **state,
            "teamCount": (
                2 if int(state["teamCount"]) == TEAM_COUNT else 99
            ),
        }
        for pair, state in snapshot["targets"].items()
    }
    mapped_owned = {
        pair for pair in snapshot["owned"] if pair[0] in actions
    }
    return {**snapshot, "owned": mapped_owned, "targets": targets}


def audit_routes(
    snapshot: dict,
    actions: dict,
    action_provenance: dict,
    profiles: dict,
    profile_provenance: dict,
    candidate_snapshot: dict,
    connection: sqlite3.Connection,
) -> dict:
    scoped = transformed_snapshot(snapshot, actions)
    routes, coverage = base.enumerate_routes(
        scoped,
        actions,
        action_provenance,
        profiles,
        profile_provenance,
        candidate_snapshot["receiptPairs"],
    )
    coverage["acceptedScoreablePairs"] = len(snapshot["owned"])
    coverage["acceptedScoreablePairsWithSealedAction"] = len(scoped["owned"])
    coverage["acceptedScoreablePairsAwaitingActionMap"] = len(
        snapshot["owned"] - scoped["owned"]
    )
    coverage["eligibleTeamCountLayer"] = TEAM_COUNT
    coverage["eligibleTc4PairsBeforeReceiptExclusion"] = coverage.pop(
        "eligibleTc1Tc2PairsBeforeReceiptExclusion"
    )
    coverage["eligibleTc4PairsAfterAllPairExclusions"] = coverage.pop(
        "eligibleTc1Tc2PairsAfterAllPairExclusions"
    )

    lineage = base.cached_lineage(routes, candidate_snapshot, scoped)
    if lineage["readyNovelLowCandidates"]:
        raise lane.GuardFailure(
            "cached exact novel tc4 candidates must stage before runbooks"
        )

    for route in routes:
        route["anchor"] = base.select_anchor(
            connection,
            route["sourcePair"],
            lineage["testedSourceKeys"],
            lineage["testedSourceHashes"],
        )
        route["targetTeamCounts"] = {
            pair_text(pair): snapshot["targets"][pair]["teamCount"]
            for pair in route["lowOutcomes"]
        }

    deterministic = [
        route
        for route in routes
        if route["deterministic"]
        and len(route["lowOutcomes"]) == 1
        and int(
            snapshot["targets"][next(iter(route["lowOutcomes"]))][
                "teamCount"
            ]
        )
        == TEAM_COUNT
    ]
    single_orbit = [
        route for route in deterministic if int(route["orbitCount"]) == 1
    ]
    fresh_anchor = [
        route for route in single_orbit if route.get("anchor") is not None
    ]
    best_by_target: dict[tuple[str, int], dict] = {}
    for route in sorted(fresh_anchor, key=lambda row: route_rank(row, snapshot)):
        best_by_target.setdefault(next(iter(route["lowOutcomes"])), route)
    selected = sorted(
        best_by_target.values(), key=lambda row: route_rank(row, snapshot)
    )
    return {
        "coverage": coverage,
        "lineage": lineage,
        "deterministicRoutes": deterministic,
        "singleOrbitRoutes": single_orbit,
        "freshAnchorRoutes": fresh_anchor,
        "selectedRoutes": selected,
    }


def make_runbooks(census: dict, snapshot: dict) -> list[dict]:
    runbooks = []
    for index, route in enumerate(census["selectedRoutes"], start=1):
        source_pair = route["sourcePair"]
        target_pair = next(iter(route["lowOutcomes"]))
        anchor = route["anchor"]
        route_id = (
            f"hc4_{index:03d}_{source_pair[0]}_r{source_pair[1]}_to_"
            f"{target_pair[0]}_r{target_pair[1]}"
        )
        output = DATA / f"low_contention_{route_id}_result.jsonl"
        temporary = output.with_suffix(output.suffix + ".tmp")
        if output.exists() or temporary.exists():
            raise lane.GuardFailure(f"tc4 output already exists: {output}")
        action_path = ROOT / route["actionArtifact"]["path"]
        command = [
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
        ]
        state = snapshot["targets"][target_pair]
        runbooks.append(
            {
                "routeId": route_id,
                "status": "ready_waiting_for_root_heavy_clearance",
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
                    "teamCountAtSeal": TEAM_COUNT,
                    "discoveredAtSeal": state["discovered"],
                    "minimumDiscAbsAtSeal": state["minimumDiscAbs"],
                    "generatedAtSeal": state["generatedAt"],
                    "projectedMarginalScoreExact": "1/16",
                    "projectionQualifier": (
                        "upper_bound_before_discriminant_penalty"
                    ),
                },
                "exactAction": {
                    "orbitIndex": route["orbitIndex"],
                    "length24OrbitCount": 1,
                    "deterministicAcrossCompatibleClasses": True,
                    "compatibleClassCount": route["compatibleClassCount"],
                    "proofKind": route["proofKind"],
                    "actionArtifact": route["actionArtifact"],
                    "profileArtifacts": route["profileArtifacts"],
                },
                "guards": {
                    "outputAbsentAtSeal": not output.exists(),
                    "outputTemporaryAbsentAtSeal": not temporary.exists(),
                    "targetTeamCountExactAtSeal": (
                        int(state["teamCount"]) == TEAM_COUNT
                    ),
                    "targetNonbaselineAtSeal": (
                        target_pair not in snapshot["baseline"]
                    ),
                    "targetUnownedAtSeal": target_pair not in snapshot["owned"],
                    "targetNotKnownVerificationPairAtSeal": (
                        target_pair not in snapshot["knownPairs"]
                    ),
                    "targetNotReceiptPairAtSeal": True,
                    "sourceAcceptedScoreableAtSeal": True,
                    "sourceAnchorFreshUntestedAtSeal": True,
                    "sourceHashPinned": True,
                    "postRunMustRecheckTargetReceiptAndOutputHash": True,
                    "submissionAuthorized": False,
                },
                "output": str(output.relative_to(ROOT)),
                "heavyCommand": command,
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


def main() -> int:
    if CERTIFICATE.exists() or SUMMARY.exists():
        raise FileExistsError("refusing to overwrite tc4 route audit")

    tc3_seal = lane.read_json(TC3_SEAL)
    expected_status = "sealed_23_of_23_exact_candidates_receipt_mapped"
    if (
        tc3_seal.get("status") != expected_status
        or int(tc3_seal.get("routeCount", -1)) != 23
        or int(tc3_seal.get("distinctCandidateHashes", -1)) != 23
        or int(tc3_seal.get("distinctTargetPairs", -1)) != 23
        or not all((tc3_seal.get("checks") or {}).values())
    ):
        raise lane.GuardFailure("tc3 frontier is not sealed 23/23")

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
        census = audit_routes(
            snapshot,
            actions,
            action_provenance,
            profiles,
            profile_provenance,
            candidate_snapshot,
            connection,
        )
    finally:
        connection.close()

    runbooks = make_runbooks(census, snapshot)
    score_total = Fraction(len(runbooks), 16)
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
    targets = {
        next(iter(route["lowOutcomes"]))
        for route in census["freshAnchorRoutes"]
    }
    all_guards = [
        value
        for runbook in runbooks
        for key, value in runbook["guards"].items()
        if key != "submissionAuthorized"
    ]
    tc4_census = {
        "deterministicTc4Routes": len(census["deterministicRoutes"]),
        "deterministicSingleOrbitTc4Routes": len(census["singleOrbitRoutes"]),
        "deterministicSingleOrbitTc4RoutesWithFreshAnchor": len(
            census["freshAnchorRoutes"]
        ),
        "distinctExecutableTargets": len(targets),
        "runbooks": len(runbooks),
    }
    certificate = {
        "schemaVersion": "low-contention-unordered-pair-route-audit-v1",
        "scope": "deterministic_tc4_fresh_untested_anchors",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_light_only_runbooks_ready"
            if runbooks
            else "certified_light_only_no_executable_runbooks"
        ),
        "method": (
            "sealed pair actions/profiles + exact tested-anchor receipt "
            "lineage through sealed tc3 frontier + current tc4 target layer"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "tc3FrontierSeal": lane.artifact(TC3_SEAL),
        "boundary": {
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "acceptedPairSetSha256": base.canonical_digest(accepted_rows),
            "targetRows": len(snapshot["targets"]),
            "targetSnapshotSha256": base.canonical_digest(target_rows),
            "targetGeneratedAtMin": min(
                state["generatedAt"] for state in snapshot["targets"].values()
            ),
            "targetGeneratedAtMax": max(
                state["generatedAt"] for state in snapshot["targets"].values()
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
        "coverage": census["coverage"],
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
        "testedAnchorExclusion": {
            "testedSourceKeys": len(census["lineage"]["testedSourceKeys"]),
            "testedSourceHashes": len(
                census["lineage"]["testedSourceHashes"]
            ),
            "cachedExactMissClosures": census["lineage"][
                "closedAnchorCount"
            ],
            "cachedNovelReadyCandidates": 0,
        },
        "tc4Census": tc4_census,
        "ranking": {
            "order": [
                "fresh accepted anchor required",
                "shorter accepted source coefficient encoding",
                "smaller current target minimum discriminant",
                "target/source/orbit order",
            ],
            "scoreFormula": (
                "1/16 per tc4 target, upper bound before discriminant penalty"
            ),
        },
        "runbooks": runbooks,
        "runbookProjection": {
            "count": len(runbooks),
            "teamCountDistribution": {"4": len(runbooks)} if runbooks else {},
            "marginalScoreExact": base.fraction_text(score_total),
            "qualifier": "upper_bound_before_discriminant_penalty",
        },
        "checks": {
            "tc3FrontierSealed23Of23": True,
            "cachedNovelTc4CandidatesAbsent": not census["lineage"][
                "readyNovelLowCandidates"
            ],
            "allRunbooksDeterministicSingleOrbitTc4": all(
                row["target"]["teamCountAtSeal"] == TEAM_COUNT
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
            "allTargetsNonbaselineUnownedUnknownNonreceipt": all(all_guards),
            "allOutputsAbsent": all(
                row["guards"]["outputAbsentAtSeal"]
                and row["guards"]["outputTemporaryAbsentAtSeal"]
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
        raise lane.GuardFailure("coefficient payload entered tc4 audit")
    lane.exclusive_text(CERTIFICATE, rendered)

    summary = {
        "schemaVersion": "low-contention-deterministic-tc4-summary-v1",
        "status": certificate["status"],
        "certificate": lane.artifact(CERTIFICATE),
        **tc4_census,
        "projectedMarginalScoreExact": certificate["runbookProjection"][
            "marginalScoreExact"
        ],
        "bestRouteId": runbooks[0]["routeId"] if runbooks else None,
        "targetRows": len(snapshot["targets"]),
        "receiptCount": receipt_audit["receiptCount"],
        "receiptPolynomialHashes": receipt_audit["receiptPolynomialHashes"],
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
