#!/usr/bin/env python3
"""Light-only deterministic tc3 audit, falling back to tc4 only if empty."""

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
TC2_SEAL = DATA / "low_contention_tc2_frontier_receipt_mapping.json"
CERTIFICATE = DATA / "low_contention_higher_tc_routes_certificate.json"
SUMMARY = DATA / "low_contention_higher_tc_routes_summary.json"


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


def transformed_snapshot(snapshot: dict, actions: dict, team_count: int) -> dict:
    # Reuse the rigorously tested tc1/tc2 eligibility engine by mapping exactly
    # the desired target layer to its accepted "2" sentinel.  Every emitted
    # route is subsequently checked/restored against the untouched snapshot.
    targets = {
        pair: {**state, "teamCount": 2 if int(state["teamCount"]) == team_count else 99}
        for pair, state in snapshot["targets"].items()
    }
    mapped_owned = {pair for pair in snapshot["owned"] if pair[0] in actions}
    return {**snapshot, "owned": mapped_owned, "targets": targets}


def census_for_count(
    team_count: int,
    snapshot: dict,
    actions: dict,
    action_provenance: dict,
    profiles: dict,
    profile_provenance: dict,
    candidate_snapshot: dict,
    connection: sqlite3.Connection,
) -> dict:
    scoped = transformed_snapshot(snapshot, actions, team_count)
    routes, coverage = base.enumerate_routes(
        scoped,
        actions,
        action_provenance,
        profiles,
        profile_provenance,
        candidate_snapshot["receiptPairs"],
    )
    lineage = base.cached_lineage(routes, candidate_snapshot, scoped)
    if lineage["readyNovelLowCandidates"]:
        raise lane.GuardFailure(
            f"cached exact novel tc{team_count} candidates must stage before runbooks"
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
        route for route in routes
        if route["deterministic"]
        and len(route["lowOutcomes"]) == 1
        and int(snapshot["targets"][next(iter(route["lowOutcomes"]))]["teamCount"])
        == team_count
    ]
    single_orbit = [route for route in deterministic if int(route["orbitCount"]) == 1]
    executable = [route for route in single_orbit if route.get("anchor") is not None]
    best_by_target = {}
    for route in sorted(executable, key=lambda row: route_rank(row, snapshot)):
        best_by_target.setdefault(next(iter(route["lowOutcomes"])), route)
    selected = sorted(best_by_target.values(), key=lambda row: route_rank(row, snapshot))
    return {
        "teamCount": team_count,
        "coverage": coverage,
        "lineage": lineage,
        "deterministicRoutes": deterministic,
        "singleOrbitRoutes": single_orbit,
        "executableRoutes": executable,
        "selectedRoutes": selected,
    }


def make_runbooks(census: dict, snapshot: dict) -> list[dict]:
    team_count = int(census["teamCount"])
    score = Fraction(1, 2**team_count)
    runbooks = []
    for index, route in enumerate(census["selectedRoutes"], start=1):
        source_pair = route["sourcePair"]
        target_pair = next(iter(route["lowOutcomes"]))
        anchor = route["anchor"]
        route_id = (
            f"hc{team_count}_{index:03d}_{source_pair[0]}_r{source_pair[1]}_to_"
            f"{target_pair[0]}_r{target_pair[1]}"
        )
        output = DATA / f"low_contention_{route_id}_result.jsonl"
        temporary = output.with_suffix(output.suffix + ".tmp")
        if output.exists() or temporary.exists():
            raise lane.GuardFailure(f"higher-tc output already exists: {output}")
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
                "teamCountAtSeal": team_count,
                "discoveredAtSeal": state["discovered"],
                "minimumDiscAbsAtSeal": state["minimumDiscAbs"],
                "generatedAtSeal": state["generatedAt"],
                "projectedMarginalScoreExact": base.fraction_text(score),
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
                "targetTeamCountExactAtSeal": True,
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
        raise FileExistsError("refusing to overwrite higher-tc route audit")
    tc2_seal = lane.read_json(TC2_SEAL)
    if tc2_seal.get("status") != "sealed_14_of_14_exact_candidates_receipt_mapped":
        raise lane.GuardFailure("tc2 frontier is not sealed 14/14")
    actions, action_provenance, action_artifacts = base.load_sealed_actions()
    profiles, profile_provenance, profile_artifacts = base.load_profiles(
        actions, action_artifacts
    )
    connection = sqlite3.connect(f"file:{lane.DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = base.load_ledger_snapshot(connection)
        candidate_snapshot = base.exact_candidate_and_receipt_snapshot(connection)
        tc3 = census_for_count(
            3, snapshot, actions, action_provenance, profiles,
            profile_provenance, candidate_snapshot, connection,
        )
        if tc3["selectedRoutes"]:
            selected_census = tc3
            tc4 = None
        else:
            tc4 = census_for_count(
                4, snapshot, actions, action_provenance, profiles,
                profile_provenance, candidate_snapshot, connection,
            )
            selected_census = tc4
    finally:
        connection.close()

    runbooks = make_runbooks(selected_census, snapshot)
    team_count = int(selected_census["teamCount"])
    score_total = Fraction(len(runbooks), 2**team_count)
    receipt_audit = candidate_snapshot["receiptAudit"]
    target_rows = [
        [label, r, state["teamCount"], state["discovered"], state["minimumDiscAbs"], state["generatedAt"]]
        for (label, r), state in sorted(snapshot["targets"].items(), key=lambda item: base.pair_sort_key(item[0]))
    ]
    accepted_rows = [
        [label, r] for label, r in sorted(snapshot["owned"], key=base.pair_sort_key)
    ]

    def census_summary(value: dict | None) -> dict | None:
        if value is None:
            return None
        return {
            "teamCount": value["teamCount"],
            "deterministicRoutes": len(value["deterministicRoutes"]),
            "deterministicSingleOrbitRoutes": len(value["singleOrbitRoutes"]),
            "deterministicSingleOrbitRoutesWithFreshAnchor": len(value["executableRoutes"]),
            "distinctExecutableTargets": len({next(iter(route["lowOutcomes"])) for route in value["executableRoutes"]}),
            "selectedRunbooks": len(value["selectedRoutes"]),
            "testedSourceKeys": len(value["lineage"]["testedSourceKeys"]),
            "testedSourceHashes": len(value["lineage"]["testedSourceHashes"]),
            "cachedNovelReadyCandidates": len(value["lineage"]["readyNovelLowCandidates"]),
        }

    certificate = {
        "schemaVersion": "low-contention-unordered-pair-route-audit-v1",
        "scope": f"deterministic_tc{team_count}_fresh_untested_anchors",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_light_only_runbooks_ready" if runbooks else "certified_light_only_no_executable_runbooks",
        "method": "sealed pair actions/profiles + exact tc1/tc2 tested-anchor receipt lineage + current target layer",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "tc2FrontierSeal": lane.artifact(TC2_SEAL),
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
        "receiptExclusion": {
            "receiptCount": receipt_audit["receiptCount"],
            "receiptPolynomialHashes": receipt_audit["receiptPolynomialHashes"],
            "receiptTargetPairs": receipt_audit["receiptTargetPairs"],
            "queuedPossiblePairMapRows": receipt_audit["queuedPossiblePairMapRows"],
        },
        "tc3Census": census_summary(tc3),
        "tc4FallbackEvaluated": tc4 is not None,
        "tc4Census": census_summary(tc4),
        "selectedTeamCount": team_count,
        "ranking": {
            "order": [
                "tc3 before tc4 fallback",
                "fresh accepted anchor required",
                "shorter accepted source coefficient encoding",
                "smaller current target minimum discriminant",
                "target/source/orbit order",
            ],
            "scoreFormula": f"1/{2**team_count} per tc{team_count} target, upper bound before discriminant penalty",
        },
        "runbooks": runbooks,
        "runbookProjection": {
            "count": len(runbooks),
            "teamCountDistribution": {str(team_count): len(runbooks)} if runbooks else {},
            "marginalScoreExact": base.fraction_text(score_total),
            "qualifier": "upper_bound_before_discriminant_penalty",
        },
        "checks": {
            "tc2FrontierSealed14Of14": True,
            "tc4EvaluatedOnlyIfTc3Empty": tc4 is None or not tc3["selectedRoutes"],
            "allRunbooksDeterministicSingleOrbitSelectedTeamCount": True,
            "allRunbookAnchorsAcceptedScoreableFreshUntested": True,
            "allTargetsNonbaselineUnownedUnknownNonreceipt": True,
            "allOutputsAbsent": True,
            "certificateContainsNoCoefficientPayload": True,
        },
        "sideEffects": {
            "sageRuns": 0, "gapRuns": 0, "polynomialArithmeticRuns": 0,
            "networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if base.COEFFICIENT_LINE_RE.search(rendered):
        raise lane.GuardFailure("coefficient payload entered higher-tc audit")
    lane.exclusive_text(CERTIFICATE, rendered)
    summary = {
        "schemaVersion": "low-contention-higher-tc-summary-v1",
        "status": certificate["status"],
        "certificate": lane.artifact(CERTIFICATE),
        "selectedTeamCount": team_count,
        "tc3Census": certificate["tc3Census"],
        "tc4FallbackEvaluated": certificate["tc4FallbackEvaluated"],
        "tc4Census": certificate["tc4Census"],
        "runbooks": len(runbooks),
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
        lane.GuardFailure, FileExistsError, ValueError, OSError,
        sqlite3.Error, json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
