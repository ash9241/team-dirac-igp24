#!/usr/bin/env python3
"""Light-only audit of deterministic tc2 pair routes from fresh anchors."""

from __future__ import annotations

import json
import shlex
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as base
import run_low_contention_sequential as lane


ROOT = lane.ROOT
DATA = lane.DATA
CERTIFICATE = DATA / "low_contention_tc2_routes_certificate.json"
SUMMARY = DATA / "low_contention_tc2_routes_summary.json"
TC1_SEAL = DATA / "low_contention_tc1_frontier_receipt_mapping.json"


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def tc2_rank(route: dict, snapshot: dict) -> tuple:
    target_pair = next(iter(route["lowOutcomes"]))
    state = snapshot["targets"][target_pair]
    disc = int(state["minimumDiscAbs"] or 10**1000)
    anchor = route["anchor"]
    return (
        int(anchor["coefficientBytes"]),
        disc,
        *base.pair_sort_key(target_pair),
        *base.pair_sort_key(route["sourcePair"]),
        int(route["orbitIndex"]),
    )


def make_runbooks(routes: list[dict], snapshot: dict) -> list[dict]:
    best_by_target = {}
    for route in sorted(routes, key=lambda row: tc2_rank(row, snapshot)):
        target_pair = next(iter(route["lowOutcomes"]))
        best_by_target.setdefault(target_pair, route)
    selected = sorted(best_by_target.values(), key=lambda row: tc2_rank(row, snapshot))
    runbooks = []
    for index, route in enumerate(selected, start=1):
        source_pair = route["sourcePair"]
        target_pair = next(iter(route["lowOutcomes"]))
        anchor = route["anchor"]
        route_id = (
            f"lc2_{index:03d}_{source_pair[0]}_r{source_pair[1]}_to_"
            f"{target_pair[0]}_r{target_pair[1]}"
        )
        output = DATA / f"low_contention_{route_id}_result.jsonl"
        temporary = output.with_suffix(output.suffix + ".tmp")
        if output.exists() or temporary.exists():
            raise lane.GuardFailure(f"tc2 output already exists: {output}")
        action_path = ROOT / route["actionArtifact"]["path"]
        command = [
            "/usr/local/bin/sage", "-python", "pair_sum_one.sage.py",
            anchor["submissionId"], str(anchor["polynomialIndex"]),
            "--orbit-map", str(action_path.relative_to(ROOT)),
            "--expected-source-hash", anchor["coefficientSha256"],
            "--expected-target", target_pair[0],
            "--output-jsonl", str(output.relative_to(ROOT)),
        ]
        state = snapshot["targets"][target_pair]
        runbooks.append({
            "routeId": route_id,
            "status": "ready_waiting_for_heavy_slot_and_fresh_refresh",
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
                "teamCountAtSeal": 2,
                "discoveredAtSeal": state["discovered"],
                "minimumDiscAbsAtSeal": state["minimumDiscAbs"],
                "generatedAtSeal": state["generatedAt"],
                "projectedMarginalScoreExact": "1/4",
                "projectionQualifier": "upper_bound_before_discriminant_penalty",
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
                "outputAbsentAtSeal": True,
                "outputTemporaryAbsentAtSeal": True,
                "targetTeamCountExactlyTwoAtSeal": True,
                "targetNonbaselineAtSeal": target_pair not in snapshot["baseline"],
                "targetUnownedAtSeal": target_pair not in snapshot["owned"],
                "targetNotKnownVerificationPairAtSeal": target_pair not in snapshot["knownPairs"],
                "targetNotReceiptPairAtSeal": True,
                "sourceAcceptedScoreableAtSeal": True,
                "sourceAnchorFreshUntestedAtSeal": True,
                "sourceHashPinned": True,
                "postRunMustRecheckTargetReceiptAndOutputHash": True,
                "submissionAuthorized": False,
            },
            "output": str(output.relative_to(ROOT)),
            "heavyCommand": command,
            "guardedCommandDisplay": " ".join(shlex.quote(value) for value in command),
            "postflightCommand": [
                "python3", "validate_low_contention_pair_route.py",
                "--certificate", str(CERTIFICATE.relative_to(ROOT)),
                "--route-id", route_id,
                "--result", str(output.relative_to(ROOT)),
            ],
        })
    return runbooks


def main() -> int:
    if CERTIFICATE.exists() or SUMMARY.exists():
        raise FileExistsError("refusing to overwrite tc2 route audit")
    tc1_seal = lane.read_json(TC1_SEAL)
    if tc1_seal.get("status") != "sealed_12_of_12_exact_candidates_receipt_mapped":
        raise lane.GuardFailure("tc1 deterministic frontier is not sealed")

    actions, action_provenance, action_artifacts = base.load_sealed_actions()
    profiles, profile_provenance, profile_artifacts = base.load_profiles(
        actions, action_artifacts
    )
    connection = sqlite3.connect(f"file:{lane.DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = base.load_ledger_snapshot(connection)
        candidate_snapshot = base.exact_candidate_and_receipt_snapshot(connection)
        # Newly accepted tc1 outputs are valid target exclusions but do not yet
        # have a sealed unordered-pair action in the v14 action chain.  They
        # must not be promoted as sources until a later action-map delta seals
        # them.  `knownPairs` remains complete, so every such pair is still
        # excluded from the target layer.
        mapped_owned = {
            pair for pair in snapshot["owned"] if pair[0] in actions
        }
        route_snapshot = {**snapshot, "owned": mapped_owned}
        routes, coverage = base.enumerate_routes(
            route_snapshot,
            actions,
            action_provenance,
            profiles,
            profile_provenance,
            candidate_snapshot["receiptPairs"],
        )
        coverage["acceptedScoreablePairs"] = len(snapshot["owned"])
        coverage["acceptedScoreablePairsWithSealedAction"] = len(mapped_owned)
        coverage["acceptedScoreablePairsAwaitingActionMap"] = len(
            snapshot["owned"] - mapped_owned
        )
        lineage = base.cached_lineage(routes, candidate_snapshot, route_snapshot)
        if lineage["readyNovelLowCandidates"]:
            raise lane.GuardFailure("cached exact novel low-contention candidates must stage first")
        for route in routes:
            route["targetTeamCounts"] = {
                pair_text(pair): snapshot["targets"][pair]["teamCount"]
                for pair in route["lowOutcomes"]
            }
            route["anchor"] = base.select_anchor(
                connection,
                route["sourcePair"],
                lineage["testedSourceKeys"],
                lineage["testedSourceHashes"],
            )
    finally:
        connection.close()

    deterministic_tc2 = [
        route for route in routes
        if route["deterministic"]
        and len(route["lowOutcomes"]) == 1
        and snapshot["targets"][next(iter(route["lowOutcomes"]))]["teamCount"] == 2
    ]
    single_orbit_tc2 = [
        route for route in deterministic_tc2 if int(route["orbitCount"]) == 1
    ]
    executable = [
        route for route in single_orbit_tc2 if route.get("anchor") is not None
    ]
    runbooks = make_runbooks(executable, snapshot)
    receipt_audit = candidate_snapshot["receiptAudit"]
    target_rows = [
        [label, r, state["teamCount"], state["discovered"], state["minimumDiscAbs"], state["generatedAt"]]
        for (label, r), state in sorted(snapshot["targets"].items(), key=lambda item: base.pair_sort_key(item[0]))
    ]
    accepted_rows = [
        [label, r] for label, r in sorted(snapshot["owned"], key=base.pair_sort_key)
    ]
    projected = Fraction(len(runbooks), 4)
    certificate = {
        # Deliberately compatible with the existing exact postflight validator.
        "schemaVersion": "low-contention-unordered-pair-route-audit-v1",
        "scope": "deterministic_tc2_fresh_untested_anchors",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_light_only_runbooks_ready",
        "method": "sealed pair actions/profiles + exact tc1 tested-anchor lineage + current ledger/receipts/targets",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "tc1FrontierSeal": lane.artifact(TC1_SEAL),
        "boundary": {
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "acceptedPairSetSha256": base.canonical_digest(accepted_rows),
            "targetRows": len(snapshot["targets"]),
            "targetSnapshotSha256": base.canonical_digest(target_rows),
            "targetGeneratedAtMin": min(state["generatedAt"] for state in snapshot["targets"].values()),
            "targetGeneratedAtMax": max(state["generatedAt"] for state in snapshot["targets"].values()),
        },
        "artifacts": {
            "sealedActionMaps": action_artifacts,
            "profileArtifacts": profile_artifacts,
            "candidateCorpus": candidate_snapshot["corpus"],
            "database": {"path": str(lane.DB.relative_to(ROOT))},
            "receiptsDirectory": str(lane.RECEIPTS.relative_to(ROOT)),
            "auditProgram": lane.artifact(Path(__file__).resolve()),
        },
        "coverage": coverage,
        "receiptExclusion": {
            "receiptCount": receipt_audit["receiptCount"],
            "receiptPolynomialHashes": receipt_audit["receiptPolynomialHashes"],
            "receiptTargetPairs": receipt_audit["receiptTargetPairs"],
            "queuedPossiblePairMapRows": receipt_audit["queuedPossiblePairMapRows"],
        },
        "testedAnchorExclusion": {
            "testedSourceKeys": len(lineage["testedSourceKeys"]),
            "testedSourceHashes": len(lineage["testedSourceHashes"]),
            "cachedExactMissClosures": lineage["closedAnchorCount"],
            "cachedNovelReadyCandidates": 0,
        },
        "tc2Census": {
            "deterministicTc2Routes": len(deterministic_tc2),
            "deterministicSingleOrbitTc2Routes": len(single_orbit_tc2),
            "deterministicSingleOrbitTc2RoutesWithFreshAnchor": len(executable),
            "distinctExecutableTargets": len({next(iter(route["lowOutcomes"])) for route in executable}),
            "runbooks": len(runbooks),
        },
        "ranking": {
            "order": [
                "fresh accepted anchor required",
                "shorter accepted source coefficient encoding",
                "smaller current target minimum discriminant",
                "target/source/orbit order",
            ],
            "scoreFormula": "1/4 per tc2 target, upper bound before discriminant penalty",
        },
        "runbooks": runbooks,
        "runbookProjection": {
            "count": len(runbooks),
            "teamCountDistribution": {"2": len(runbooks)} if runbooks else {},
            "marginalScoreExact": (
                str(projected.numerator) if projected.denominator == 1
                else f"{projected.numerator}/{projected.denominator}"
            ),
            "qualifier": "upper_bound_before_discriminant_penalty",
        },
        "checks": {
            "tc1FrontierSealed12Of12": True,
            "allRunbooksDeterministicSingleOrbitTc2": True,
            "allRunbookAnchorsAcceptedScoreableFreshUntested": True,
            "allTargetsNonbaselineUnownedUnknownNonreceipt": True,
            "allOutputsAbsent": True,
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
        raise lane.GuardFailure("coefficient payload entered tc2 audit")
    lane.exclusive_text(CERTIFICATE, rendered)
    summary = {
        "schemaVersion": "low-contention-deterministic-tc2-summary-v1",
        "status": certificate["status"],
        "certificate": lane.artifact(CERTIFICATE),
        **certificate["tc2Census"],
        "projectedMarginalScoreExact": certificate["runbookProjection"]["marginalScoreExact"],
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
