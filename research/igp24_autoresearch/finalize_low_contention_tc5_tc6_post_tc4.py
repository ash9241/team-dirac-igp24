#!/usr/bin/env python3
"""Finalize the tc5/tc6 light frontier after the exact tc4 receipt.

The finalizer repeats the complete offline route audit against the current
read-only ledger, target cache, exact candidate corpus, and all intact local
receipts.  It also pins and verifies the tc4 receipt/manifest/candidate mapping
before emitting any surviving runbook.  No polynomial arithmetic, network
operation, ledger write, or submission is performed.
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
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_census


ROOT = lane.ROOT
DATA = lane.DATA
RECEIPTS = lane.RECEIPTS
TEAM_COUNTS = (5, 6)
TC4_SUBMISSION_ID = "sub_b4f7f2bd853446aca3382d22345a73ec"
TC4_RECEIPT = RECEIPTS / f"{TC4_SUBMISSION_ID}.json"
TC4_MAPPING = DATA / "low_contention_tc4_receipt_mapping_ready.json"
PROVISIONAL_CERTIFICATE = (
    DATA / "low_contention_tc5_tc6_routes_certificate.json"
)
CERTIFICATE = (
    DATA / "low_contention_tc5_tc6_post_tc4_routes_certificate.json"
)
SUMMARY = DATA / "low_contention_tc5_tc6_post_tc4_routes_summary.json"


def receipt_manifest_hashes(receipt: dict) -> set[str]:
    manifest = Path(str(receipt["manifest"])).expanduser().resolve()
    if not manifest.is_file():
        raise lane.GuardFailure("tc4 receipt manifest is absent")
    if exact_census.sha256_path(manifest) != str(receipt["manifestHash"]):
        raise lane.GuardFailure("tc4 receipt manifest hash changed")
    hashes = set()
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        line = exact_census.canonical_polynomial_line(raw_line.strip())
        if line is not None:
            hashes.add(exact_census.sha256_bytes(line.encode("ascii")))
    if len(hashes) != 22:
        raise lane.GuardFailure("tc4 receipt does not pin 22 distinct hashes")
    return hashes


def validate_tc4_receipt() -> tuple[dict, dict, set[str]]:
    receipt = lane.read_json(TC4_RECEIPT)
    response = receipt.get("response") or {}
    if (
        receipt.get("commit") is not True
        or int(receipt.get("polynomials", -1)) != 22
        or str(response.get("submissionId")) != TC4_SUBMISSION_ID
        or int(response.get("rejectedCount", -1)) != 0
        or int(response.get("queuedCount", 0))
        + len(response.get("verifiedPolynomials") or [])
        != 22
        or response.get("failedPolynomials")
    ):
        raise lane.GuardFailure("tc4 receipt is incomplete or rejected/failed")

    mapping = lane.read_json(TC4_MAPPING)
    mappings = mapping.get("mappings") or []
    if (
        mapping.get("status")
        != "ready_for_receipt_mapping_after_submission"
        or int(mapping.get("routeCount", -1)) != 22
        or int(mapping.get("distinctCandidateHashes", -1)) != 22
        or int(mapping.get("distinctTargetPairs", -1)) != 22
        or len(mappings) != 22
        or str((mapping.get("combinedManifest") or {}).get("sha256"))
        != str(receipt.get("manifestHash"))
    ):
        raise lane.GuardFailure("tc4 staged mapping does not match receipt")

    manifest_hashes = receipt_manifest_hashes(receipt)
    mapping_hashes = {
        str(row.get("candidateSha256", "")) for row in mappings
    }
    mapping_pairs = {
        str(row.get("targetPair", "")) for row in mappings
    }
    if (
        mapping_hashes != manifest_hashes
        or len(mapping_pairs) != 22
        or any(not value for value in mapping_pairs)
    ):
        raise lane.GuardFailure("tc4 receipt hash/pair mapping changed")
    return receipt, mapping, manifest_hashes


def finalize_runbooks(
    censuses: list[dict], snapshot: dict
) -> list[dict]:
    runbooks = scout.make_runbooks(censuses, snapshot)
    for row in runbooks:
        row["status"] = (
            "finalized_receipt_aware_waiting_for_root_heavy_clearance"
        )
        row["guards"].pop("postTc4FullAuditRerunRequired", None)
        row["guards"].update(
            {
                "tc4ReceiptHashesExcludedAtSeal": True,
                "targetPairRevalidatedAfterTc4": True,
                "sourceAnchorRevalidatedAfterTc4": True,
                "currentBoundaryMustRemainUnchangedAtExecution": True,
            }
        )
        command = row["postflightCommand"]
        certificate_index = command.index("--certificate") + 1
        command[certificate_index] = str(CERTIFICATE.relative_to(ROOT))
    return runbooks


def runbook_identity(row: dict) -> tuple:
    return (
        str(row["routeId"]),
        str(row["source"]["coefficientSha256"]),
        str(row["target"]["label"]),
        int(row["target"]["r"]),
        int(row["target"]["teamCountAtSeal"]),
    )


def census_summary(census: dict) -> dict:
    targets = {
        next(iter(route["lowOutcomes"]))
        for route in census["executableRoutes"]
    }
    return {
        "teamCount": census["teamCount"],
        "deterministicRoutes": len(census["deterministicRoutes"]),
        "deterministicSingleOrbitRoutes": len(census["singleOrbitRoutes"]),
        "deterministicSingleOrbitRoutesWithFreshAnchor": len(
            census["executableRoutes"]
        ),
        "distinctExecutableTargets": len(targets),
        "selectedRunbooks": len(census["selectedRoutes"]),
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
        raise FileExistsError("refusing to overwrite finalized tc5/tc6 audit")

    provisional = lane.read_json(PROVISIONAL_CERTIFICATE)
    if not all((provisional.get("checks") or {}).values()):
        raise lane.GuardFailure("provisional tc5/tc6 audit was not certified")
    receipt, mapping, tc4_hashes = validate_tc4_receipt()

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
        if not tc4_hashes <= candidate_snapshot["receiptHashes"]:
            raise lane.GuardFailure(
                "tc4 hashes are not all excluded by current receipts"
            )
        tc4_pairs = set()
        for digest in tc4_hashes:
            pairs = candidate_snapshot["candidatePairIndex"].get(digest, set())
            if len(pairs) != 1:
                raise lane.GuardFailure(
                    "tc4 receipt hash lacks one exact candidate pair"
                )
            tc4_pairs.update(pairs)
        if len(tc4_pairs) != 22 or not tc4_pairs <= candidate_snapshot[
            "receiptPairs"
        ]:
            raise lane.GuardFailure(
                "tc4 exact receipt pairs are not fully excluded"
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
            censuses.append(scout.rerank_census(census, snapshot))
    finally:
        connection.close()

    runbooks = finalize_runbooks(censuses, snapshot)
    identities = [runbook_identity(row) for row in runbooks]
    if len(identities) != len(set(identities)):
        raise lane.GuardFailure("final tc5/tc6 identities are not distinct")
    target_pairs = {
        (row["target"]["label"], int(row["target"]["r"]))
        for row in runbooks
    }
    if len(target_pairs) != len(runbooks):
        raise lane.GuardFailure("final tc5/tc6 targets are not distinct")

    provisional_runbooks = provisional.get("runbooks") or []
    provisional_identities = {
        runbook_identity(row) for row in provisional_runbooks
    }
    current_identities = set(identities)
    provisional_routes = {
        str(row["routeId"]): runbook_identity(row)
        for row in provisional_runbooks
    }
    current_routes = {
        str(row["routeId"]): runbook_identity(row) for row in runbooks
    }
    changed_anchor_route_ids = sorted(
        route_id
        for route_id in provisional_routes.keys() & current_routes.keys()
        if provisional_routes[route_id] != current_routes[route_id]
    )
    delta = {
        "provisionalRunbooks": len(provisional_identities),
        "finalRunbooks": len(current_identities),
        "exactIdentitySurvivors": len(
            provisional_identities & current_identities
        ),
        "droppedExactIdentities": len(
            provisional_identities - current_identities
        ),
        "newlyAuditedExactIdentities": len(
            current_identities - provisional_identities
        ),
        "changedAnchorRouteIds": changed_anchor_route_ids,
        "changedAnchorRouteCount": len(changed_anchor_route_ids),
    }

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
    layer_counts = {
        str(census["teamCount"]): len(census["selectedRoutes"])
        for census in censuses
    }
    receipt_audit = candidate_snapshot["receiptAudit"]
    all_non_submission_guards = [
        value
        for row in runbooks
        for key, value in row["guards"].items()
        if key != "submissionAuthorized"
    ]
    provisional_target_hash = str(
        (provisional.get("boundary") or {}).get("targetSnapshotSha256")
    )
    current_target_hash = base.canonical_digest(target_rows)
    certificate = {
        "schemaVersion": "low-contention-post-tc4-route-audit-v1",
        "scope": "finalized_receipt_aware_deterministic_tc5_tc6",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_final_receipt_aware_runbooks_ready"
            if runbooks
            else "certified_final_receipt_aware_no_runbooks"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "tc4Receipt": {
            **lane.artifact(TC4_RECEIPT),
            "submissionId": TC4_SUBMISSION_ID,
            "declaredPolynomials": int(receipt["polynomials"]),
            "submissionStatusAtAudit": str(
                (receipt.get("response") or {}).get("submissionStatus")
            ),
            "rejectedCount": int(
                (receipt.get("response") or {}).get("rejectedCount", 0)
            ),
            "manifestSha256": str(receipt["manifestHash"]),
            "exactHashesExcluded": len(tc4_hashes),
            "exactPairsExcluded": len(tc4_pairs),
        },
        "tc4Mapping": {
            **lane.artifact(TC4_MAPPING),
            "routeCount": int(mapping["routeCount"]),
            "distinctCandidateHashes": int(
                mapping["distinctCandidateHashes"]
            ),
            "distinctTargetPairs": int(mapping["distinctTargetPairs"]),
        },
        "provisionalAudit": lane.artifact(PROVISIONAL_CERTIFICATE),
        "boundary": {
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "acceptedPairSetSha256": base.canonical_digest(accepted_rows),
            "targetRows": len(snapshot["targets"]),
            "targetSnapshotSha256": current_target_hash,
            "targetSnapshotChangedSinceProvisional": (
                current_target_hash != provisional_target_hash
            ),
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
        "provisionalToFinalDelta": delta,
        "ranking": {
            "order": [
                "tc5 projected score before tc6",
                "identity complex-conjugation proof before exact cached profiles",
                "fewer compatible exact conjugacy classes",
                "shorter accepted source coefficient encoding",
                "smaller current target minimum discriminant",
                "target/source/orbit deterministic tie-break",
            ],
            "reliabilityQualifier": (
                "all selected routes revalidated exact, deterministic across "
                "compatible classes, and single length-24 orbit"
            ),
        },
        "runbooks": runbooks,
        "runbookProjection": {
            "count": len(runbooks),
            "teamCountDistribution": layer_counts,
            "marginalScoreExact": base.fraction_text(total_score),
            "qualifier": "upper_bound_before_discriminant_penalty",
        },
        "checks": {
            "tc4ReceiptPinnedAndIntact": True,
            "tc4ManifestHashesMatchExactMapping": True,
            "tc4ReceiptHashesExcluded": tc4_hashes
            <= candidate_snapshot["receiptHashes"],
            "tc4ExactPairsExcluded": tc4_pairs
            <= candidate_snapshot["receiptPairs"],
            "cachedNovelTc5Tc6CandidatesAbsent": all(
                not census["lineage"]["readyNovelLowCandidates"]
                for census in censuses
            ),
            "allRunbooksCurrentAcceptedFreshAnchors": all(
                row["guards"]["sourceAcceptedScoreableAtSeal"]
                and row["guards"]["sourceAnchorFreshUntestedAtSeal"]
                and row["guards"]["sourceAnchorRevalidatedAfterTc4"]
                for row in runbooks
            ),
            "allRunbooksDeterministicSingleOrbitTc5OrTc6": all(
                row["target"]["teamCountAtSeal"] in TEAM_COUNTS
                and row["exactAction"]["length24OrbitCount"] == 1
                and row["exactAction"][
                    "deterministicAcrossCompatibleClasses"
                ]
                for row in runbooks
            ),
            "allTargetsDistinct": len(target_pairs) == len(runbooks),
            "allTargetsCurrentNonbaselineUnownedUnknownNonreceipt": all(
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
            "coefficient payload entered finalized tc5/tc6 certificate"
        )
    lane.exclusive_text(CERTIFICATE, rendered)

    best = runbooks[0] if runbooks else None
    summary = {
        "schemaVersion": "low-contention-post-tc4-summary-v1",
        "status": certificate["status"],
        "certificate": lane.artifact(CERTIFICATE),
        "tc4SubmissionId": TC4_SUBMISSION_ID,
        "tc4ReceiptStatusAtAudit": certificate["tc4Receipt"][
            "submissionStatusAtAudit"
        ],
        "tc4ExactReceiptHashesExcluded": len(tc4_hashes),
        "tc4ExactReceiptPairsExcluded": len(tc4_pairs),
        "censuses": certificate["censuses"],
        "provisionalToFinalDelta": delta,
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
        "targetSnapshotChangedSinceProvisional": certificate["boundary"][
            "targetSnapshotChangedSinceProvisional"
        ],
        "acceptedScoreablePairs": len(snapshot["owned"]),
        "targetRows": len(snapshot["targets"]),
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
