#!/usr/bin/env python3
"""Execute the final receipt-aware tc5/tc6 runbooks sequentially offline.

The executor has no network or submission path.  It pins the finalized
post-tc4 ledger/target boundary and tc4 receipt lineage, launches one
synchronous Sage worker at a time, and stages only exact squarefree
single-orbit candidates that remain coefficient-, pair-, and receipt-novel.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as base
import finalize_low_contention_tc5_tc6_post_tc4 as finalizer
import run_low_contention_remaining_batch as exact
import run_low_contention_sequential as lane


ROOT = lane.ROOT
DATA = lane.DATA
OUTBOX = lane.OUTBOX
ROUTE_CERTIFICATE = DATA / "low_contention_tc5_tc6_post_tc4_routes_certificate.json"
BATCH_MANIFEST = OUTBOX / "low_contention_tc5_tc6_batch_20260722.txt"
MAPPING_READY = DATA / "low_contention_tc5_tc6_receipt_mapping_ready.json"
BATCH_CERTIFICATE = DATA / "low_contention_tc5_tc6_batch_certificate.json"
EXPECTED_TC5 = 4
EXPECTED_TC6 = 10
EXPECTED_ROUTES = EXPECTED_TC5 + EXPECTED_TC6


def boundary_snapshot(connection: sqlite3.Connection) -> dict:
    snapshot = base.load_ledger_snapshot(connection)
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
            snapshot["targets"].items(), key=lambda item: base.pair_sort_key(item[0])
        )
    ]
    accepted_rows = [
        [label, r]
        for label, r in sorted(snapshot["owned"], key=base.pair_sort_key)
    ]
    return {
        "acceptedScoreablePairs": len(accepted_rows),
        "acceptedPairSetSha256": base.canonical_digest(accepted_rows),
        "targetRows": len(target_rows),
        "targetSnapshotSha256": base.canonical_digest(target_rows),
        "targetGeneratedAtMin": min(
            state["generatedAt"] for state in snapshot["targets"].values()
        ),
        "targetGeneratedAtMax": max(
            state["generatedAt"] for state in snapshot["targets"].values()
        ),
    }


def validate_boundary(certificate: dict, actual: dict) -> None:
    sealed = certificate.get("boundary") or {}
    for key in (
        "acceptedScoreablePairs",
        "acceptedPairSetSha256",
        "targetRows",
        "targetSnapshotSha256",
        "targetGeneratedAtMin",
        "targetGeneratedAtMax",
    ):
        if str(actual.get(key)) != str(sealed.get(key)):
            raise lane.GuardFailure(f"post-tc4 execution boundary changed: {key}")


def parse_target_pair(value: str) -> tuple[str, int]:
    label, separator, rank = str(value).partition("/r")
    if not separator or not label or not rank.isdigit():
        raise lane.GuardFailure("malformed exact receipt target pair")
    return label, int(rank)


def validate_receipt_lineage(
    certificate: dict,
    receipt_hashes: set[str],
    receipt_pairs: set[tuple[str, int]],
) -> dict:
    sealed_receipt = certificate.get("tc4Receipt") or {}
    sealed_mapping = certificate.get("tc4Mapping") or {}
    if (
        lane.sha256_path(finalizer.TC4_RECEIPT) != str(sealed_receipt.get("sha256"))
        or lane.sha256_path(finalizer.TC4_MAPPING) != str(sealed_mapping.get("sha256"))
    ):
        raise lane.GuardFailure("pinned tc4 receipt/mapping artifact changed")
    receipt, mapping, exact_hashes = finalizer.validate_tc4_receipt()
    mappings = mapping.get("mappings") or []
    exact_pairs = {parse_target_pair(row["targetPair"]) for row in mappings}
    if (
        len(exact_hashes) != 22
        or len(exact_pairs) != 22
        or not exact_hashes.issubset(receipt_hashes)
        or not exact_pairs.issubset(receipt_pairs)
    ):
        raise lane.GuardFailure("tc4 exact receipt hashes/pairs are not fully excluded")
    return {
        "submissionId": finalizer.TC4_SUBMISSION_ID,
        "receipt": lane.artifact(finalizer.TC4_RECEIPT),
        "mapping": lane.artifact(finalizer.TC4_MAPPING),
        "manifestSha256": str(receipt["manifestHash"]),
        "exactHashesExcluded": len(exact_hashes),
        "exactPairsExcluded": len(exact_pairs),
    }


def validate_route(route: dict) -> None:
    lane.validate_heavy_command(route)
    action = route.get("exactAction") or {}
    action_artifact = action.get("actionArtifact") or {}
    action_path = (ROOT / str(action_artifact.get("path"))).resolve()
    if (
        int(action.get("length24OrbitCount", -1)) != 1
        or action.get("deterministicAcrossCompatibleClasses") is not True
        or int(action.get("orbitIndex", -1)) < 0
        or not action_path.is_relative_to(ROOT)
        or not action_path.is_file()
        or lane.sha256_path(action_path) != str(action_artifact.get("sha256"))
    ):
        raise lane.GuardFailure("route action/signature certificate changed")
    if route.get("routeReliability", {}).get("exactDeterministic") is not True:
        raise lane.GuardFailure("route reliability is not exact deterministic")


def guard(
    connection: sqlite3.Connection,
    route: dict,
    receipt_hashes: set[str],
    receipt_pairs: set[tuple[str, int]],
    candidate_hash: str | None = None,
) -> dict:
    source, target = route["source"], route["target"]
    pair = (str(target["label"]), int(target["r"]))
    team_count = int(target["teamCountAtSeal"])
    source_row = connection.execute(
        "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
        "FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) "
        "WHERE p.submission_id=? AND p.polynomial_index=?",
        (source["submissionId"], source["polynomialIndex"]),
    ).fetchone()
    if (
        source_row is None
        or str(source_row["coefficient_hash"]) != str(source["coefficientSha256"])
        or str(source_row["status"]) != "accepted"
        or int(source_row["scoreable"] or 0) != 1
        or str(source_row["label"]) != str(source["label"])
        or int(source_row["r"]) != int(source["r"])
    ):
        raise lane.GuardFailure("tc5/tc6 source reconstruction anchor changed")
    if connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
    ).fetchone() is not None:
        raise lane.GuardFailure("tc5/tc6 target is baseline")
    if connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", pair
    ).fetchone() is not None:
        raise lane.GuardFailure("tc5/tc6 target is already locally verified")
    state = connection.execute(
        "SELECT team_count,discovered,minimum_disc_abs,generated_at "
        "FROM targets WHERE label=? AND r=?", pair
    ).fetchone()
    if (
        state is None
        or team_count not in (5, 6)
        or int(state["team_count"]) != team_count
        or int(state["discovered"] or 0) != 1
        or str(state["minimum_disc_abs"]) != str(target["minimumDiscAbsAtSeal"])
        or str(state["generated_at"]) != str(target["generatedAtSeal"])
    ):
        raise lane.GuardFailure("tc5/tc6 target boundary changed")
    if pair in receipt_pairs:
        raise lane.GuardFailure("tc5/tc6 target is receipt-covered")
    if candidate_hash is not None:
        if candidate_hash in receipt_hashes:
            raise lane.GuardFailure("tc5/tc6 candidate hash is receipt-covered")
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (candidate_hash,),
        ).fetchone() is not None:
            raise lane.GuardFailure("tc5/tc6 candidate hash is already in the ledger")
    return {
        "sourceAcceptedScoreable": True,
        "sourceReconstructionHashPinned": True,
        "sourceAnchorFreshUntested": True,
        "targetPair": f"{pair[0]}/r{pair[1]}",
        "targetTeamCount": team_count,
        "targetDiscovered": True,
        "targetMinimumDiscAbs": str(state["minimum_disc_abs"]),
        "targetGeneratedAt": str(state["generated_at"]),
        "targetNotBaseline": True,
        "targetNotLocallyVerified": True,
        "targetNotReceiptCovered": True,
        "candidateNovel": candidate_hash is not None,
    }


def write_postflight(
    route: dict, digest: str, checked: dict, receipt_audit: dict, boundary: dict
) -> Path:
    paths = lane.planned_paths(route)
    source, target = route["source"], route["target"]
    action = route["exactAction"]
    value = {
        "schemaVersion": "low-contention-pair-route-postflight-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_novel_live_not_staged_not_submitted",
        "routeCertificate": lane.artifact(ROUTE_CERTIFICATE),
        "routeId": route["routeId"],
        "result": lane.artifact(paths["result"]),
        "source": {
            "submissionId": source["submissionId"],
            "polynomialIndex": source["polynomialIndex"],
            "label": source["label"],
            "r": source["r"],
            "coefficientSha256": source["coefficientSha256"],
            "ledgerStatus": "accepted_scoreable",
            "exactReconstructionPinned": True,
        },
        "target": {
            "label": target["label"],
            "r": target["r"],
            "teamCount": checked["targetTeamCount"],
            "discovered": True,
            "minimumDiscAbs": checked["targetMinimumDiscAbs"],
            "generatedAt": checked["targetGeneratedAt"],
        },
        "actionAssignment": {
            "proofKind": action["proofKind"],
            "compatibleClassCount": action["compatibleClassCount"],
            "deterministicAcrossCompatibleClasses": True,
            "length24OrbitCount": 1,
            "orbitIndex": action["orbitIndex"],
            "actionArtifact": action["actionArtifact"],
        },
        "candidate": {
            "coefficientSha256": digest,
            "primitiveMonicDegree24": True,
            "irreducible": True,
            "irreducibilityProof": "pinned_pair_sum_one_sage_certified_status",
            "exactSquarefreeOrbitCertificate": True,
            "exactTargetAssignment": True,
            "knownLedgerHash": False,
            "receiptHash": False,
        },
        "receiptExclusion": receipt_audit,
        "executionBoundary": boundary,
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    lane.exclusive_json(paths["postflight"], value)
    return paths["postflight"]


def write_stage(
    route: dict,
    digest: str,
    checked: dict,
    receipt_audit: dict,
    boundary: dict,
    manifest: Path,
    offline: dict,
) -> Path:
    paths = lane.planned_paths(route)
    value = {
        "schemaVersion": "low-contention-final-higher-tc-stage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_novel_tc5_tc6_staged_offline_dry_run_not_submitted",
        "routeId": route["routeId"],
        "routeCertificate": lane.artifact(ROUTE_CERTIFICATE),
        "result": lane.artifact(paths["result"]),
        "postflight": lane.artifact(paths["postflight"]),
        "manifest": {
            **lane.artifact(manifest),
            "bytes": manifest.stat().st_size,
            "polynomials": 1,
        },
        "candidate": {
            "coefficientSha256": digest,
            "targetLabel": route["target"]["label"],
            "targetR": route["target"]["r"],
            "targetTeamCount": checked["targetTeamCount"],
            "irreducible": True,
            "primitiveMonicDegree24": True,
            "exactSquarefreeSingleOrbitCertificate": True,
            "exactTargetAssignment": True,
            "knownLedgerHash": False,
            "receiptHash": False,
        },
        "targetGuard": checked,
        "receiptExclusion": receipt_audit,
        "executionBoundary": boundary,
        "offlineDryRun": offline,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "heavyWorkersLaunched": 1,
            "networkCalls": 0,
            "ledgerWrites": 0,
            "submissionCalls": 0,
        },
    }
    lane.exclusive_json(paths["stageCertificate"], value)
    return paths["stageCertificate"]


def run_route(
    route: dict,
    receipt_hashes: set[str],
    receipt_pairs: set[tuple[str, int]],
    receipt_audit: dict,
    boundary: dict,
) -> tuple[dict, str | None]:
    try:
        lane.ensure_preworker_absence(route)
        validate_route(route)
        with lane.connect_ro() as connection:
            guard(connection, route, receipt_hashes, receipt_pairs)
        completed = subprocess.run(
            list(route["heavyCommand"]),
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            tail = completed.stderr.strip().splitlines()[-1:] or ["no diagnostic"]
            raise lane.GuardFailure(f"heavy worker failed: {tail[0][:240]}")
        line, digest, _row = exact.validate_worker_result(route)
        with lane.connect_ro() as connection:
            checked = guard(connection, route, receipt_hashes, receipt_pairs, digest)
        postflight = write_postflight(route, digest, checked, receipt_audit, boundary)
        manifest = lane.ensure_manifest(route, line, digest)
        offline = exact.offline_manifest_check(manifest, [digest], receipt_hashes)
        stage = write_stage(
            route, digest, checked, receipt_audit, boundary, manifest, offline
        )
        return ({
            "routeId": route["routeId"],
            "status": "certified_exact_tc5_tc6_staged_offline_not_submitted",
            "teamCount": checked["targetTeamCount"],
            "targetPair": checked["targetPair"],
            "candidateSha256": digest,
            "result": lane.artifact(lane.planned_paths(route)["result"]),
            "postflight": lane.artifact(postflight),
            "manifest": lane.artifact(manifest),
            "stageCertificate": lane.artifact(stage),
            "workersLaunched": 1,
        }, line)
    except (
        lane.GuardFailure, ValueError, OSError, sqlite3.Error, json.JSONDecodeError
    ) as exc:
        return ({
            "routeId": route["routeId"],
            "status": "skipped_fail_closed",
            "teamCount": int(route["target"]["teamCountAtSeal"]),
            "reason": str(exc)[:500],
            "workersLaunched": int(lane.planned_paths(route)["result"].exists()),
        }, None)


def write_mapping_ready(reports: list[dict], offline: dict) -> dict:
    successes = [row for row in reports if row["status"].startswith("certified_exact_")]
    mappings = [
        {
            "manifestPosition": index,
            "routeId": row["routeId"],
            "teamCount": row["teamCount"],
            "targetPair": row["targetPair"],
            "candidateSha256": row["candidateSha256"],
            "result": row["result"],
            "postflight": row["postflight"],
            "individualManifest": row["manifest"],
            "receiptSubmissionId": None,
        }
        for index, row in enumerate(successes, start=1)
    ]
    value = {
        "schemaVersion": "low-contention-tc5-tc6-receipt-mapping-ready-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_receipt_mapping_after_submission",
        "routeCertificate": lane.artifact(ROUTE_CERTIFICATE),
        "combinedManifest": {
            **lane.artifact(BATCH_MANIFEST),
            "bytes": BATCH_MANIFEST.stat().st_size,
            "polynomials": len(successes),
        },
        "combinedOfflineDryRun": offline,
        "routeCount": len(successes),
        "teamCountDistribution": {
            "5": sum(int(row["teamCount"]) == 5 for row in successes),
            "6": sum(int(row["teamCount"]) == 6 for row in successes),
        },
        "distinctCandidateHashes": len({row["candidateSha256"] for row in successes}),
        "distinctTargetPairs": len({row["targetPair"] for row in successes}),
        "mappings": mappings,
        "receiptSubmissionId": None,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    lane.exclusive_json(MAPPING_READY, value)
    return value


def main() -> int:
    if BATCH_MANIFEST.exists() or MAPPING_READY.exists() or BATCH_CERTIFICATE.exists():
        raise lane.GuardFailure("tc5/tc6 combined batch artifact already exists")
    certificate = lane.read_json(ROUTE_CERTIFICATE)
    routes = certificate.get("runbooks") or []
    team_counts = [int(row["target"]["teamCountAtSeal"]) for row in routes]
    if (
        certificate.get("scope") != "finalized_receipt_aware_deterministic_tc5_tc6"
        or certificate.get("status") != "certified_final_receipt_aware_runbooks_ready"
        or not all((certificate.get("checks") or {}).values())
        or len(routes) != EXPECTED_ROUTES
        or str(routes[0].get("routeId"))
        != "hc5_001_24T12235_r24_to_24T12337_r24"
        or team_counts != [5] * EXPECTED_TC5 + [6] * EXPECTED_TC6
        or len({str(row["routeId"]) for row in routes}) != EXPECTED_ROUTES
    ):
        raise lane.GuardFailure("final tc5/tc6 runbook frontier changed or is not ready")

    with lane.execution_lock():
        with lane.connect_ro() as connection:
            boundary = boundary_snapshot(connection)
            validate_boundary(certificate, boundary)
            receipt_hashes, receipt_pairs, receipt_audit = lane.receipt_snapshot(connection)
        tc4_lineage = validate_receipt_lineage(
            certificate, receipt_hashes, receipt_pairs
        )
        for route in routes:
            lane.ensure_preworker_absence(route)
            validate_route(route)
            with lane.connect_ro() as connection:
                guard(connection, route, receipt_hashes, receipt_pairs)

        reports: list[dict] = []
        lines: list[str] = []
        hashes: list[str] = []
        for index, route in enumerate(routes, start=1):
            report, line = run_route(
                route, receipt_hashes, receipt_pairs, receipt_audit, boundary
            )
            reports.append(report)
            if line is not None:
                lines.append(line)
                hashes.append(str(report["candidateSha256"]))
            print(json.dumps({
                "route": index,
                "routeId": route["routeId"],
                "teamCount": int(route["target"]["teamCountAtSeal"]),
                "status": report["status"],
                "successesSoFar": len(lines),
            }, sort_keys=True), flush=True)

        with lane.connect_ro() as connection:
            ending_boundary = boundary_snapshot(connection)
        validate_boundary(certificate, ending_boundary)
        if lines:
            lane.exclusive_text(BATCH_MANIFEST, "\n".join(lines) + "\n")
            combined_offline = exact.offline_manifest_check(
                BATCH_MANIFEST, hashes, receipt_hashes
            )
            manifest_artifact = {
                **lane.artifact(BATCH_MANIFEST),
                "bytes": BATCH_MANIFEST.stat().st_size,
                "polynomials": len(lines),
            }
            mapping_ready = write_mapping_ready(reports, combined_offline)
            mapping_artifact = lane.artifact(MAPPING_READY)
        else:
            combined_offline = {
                "commit": False,
                "networkCalls": 0,
                "submissionCalls": 0,
                "polynomials": 0,
                "knownLocalHashes": 0,
                "receiptHashes": 0,
            }
            manifest_artifact = None
            mapping_ready = None
            mapping_artifact = None
        projected = sum(
            (Fraction(1, 2 ** int(row["teamCount"])) for row in reports
             if row["status"].startswith("certified_exact_")),
            Fraction(0),
        )
        batch_certificate = {
            "schemaVersion": "low-contention-final-tc5-tc6-batch-v1",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "status": (
                "certified_all_final_tc5_tc6_staged_offline_not_submitted"
                if len(lines) == EXPECTED_ROUTES
                else "completed_with_fail_closed_tc5_tc6_skips_not_submitted"
            ),
            "routeOrder": [route["routeId"] for route in routes],
            "exclusiveSequentialExecution": True,
            "maximumConcurrentSageGapWorkers": 1,
            "coordinatorHeavySlotLease": "root_authorized_batch_exclusive_slot",
            "routeCertificate": lane.artifact(ROUTE_CERTIFICATE),
            "tc4ReceiptLineage": tc4_lineage,
            "receiptExclusion": receipt_audit,
            "executionBoundaryBeforeAndAfter": boundary,
            "routes": reports,
            "successes": len(lines),
            "failClosedSkips": EXPECTED_ROUTES - len(lines),
            "teamCountDistribution": {
                "5": sum(row["status"].startswith("certified_exact_") and row["teamCount"] == 5 for row in reports),
                "6": sum(row["status"].startswith("certified_exact_") and row["teamCount"] == 6 for row in reports),
            },
            "projectedMarginalScoreExact": base.fraction_text(projected),
            "combinedManifest": manifest_artifact,
            "combinedOfflineDryRun": combined_offline,
            "receiptMappingReady": mapping_artifact,
            "receiptMappingRows": (
                int(mapping_ready["routeCount"]) if mapping_ready is not None else 0
            ),
            "submissionAuthorized": False,
            "coefficientMaterialIncluded": False,
            "credentialMaterialIncluded": False,
            "sideEffects": {
                "sageWorkersLaunched": sum(int(row["workersLaunched"]) for row in reports),
                "networkCalls": 0,
                "ledgerWrites": 0,
                "submissionCalls": 0,
            },
        }
        lane.exclusive_json(BATCH_CERTIFICATE, batch_certificate)
    print(json.dumps({
        "status": batch_certificate["status"],
        "successes": batch_certificate["successes"],
        "failClosedSkips": batch_certificate["failClosedSkips"],
        "teamCountDistribution": batch_certificate["teamCountDistribution"],
        "projectedMarginalScoreExact": batch_certificate["projectedMarginalScoreExact"],
        "combinedManifest": lane.relative(BATCH_MANIFEST) if BATCH_MANIFEST.exists() else None,
        "receiptMappingReady": lane.relative(MAPPING_READY) if MAPPING_READY.exists() else None,
        "combinedCertificate": lane.relative(BATCH_CERTIFICATE),
        "networkCalls": 0,
        "submissionCalls": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        lane.GuardFailure, ValueError, OSError, sqlite3.Error, json.JSONDecodeError
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
