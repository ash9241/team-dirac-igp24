#!/usr/bin/env python3
"""Run all sealed deterministic tc4 routes sequentially and stage offline.

There is deliberately no network or submission path.  One shared execution
lock covers a single receipt/target census and all synchronous Sage workers.
Each exact hit receives coefficient-free postflight and stage certificates,
an individually validated manifest, and an entry in receipt-mapping-ready
metadata.  The exact hits are also collected into one offline-validated
combined manifest.  Route failures are recorded and skipped closed.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import run_low_contention_remaining_batch as exact
import run_low_contention_sequential as lane


ROOT = lane.ROOT
DATA = lane.DATA
OUTBOX = lane.OUTBOX
ROUTE_CERTIFICATE = DATA / "low_contention_tc4_routes_certificate.json"
REQUIRED_TC3_SEAL = DATA / "low_contention_tc3_frontier_receipt_mapping.json"
BATCH_MANIFEST = OUTBOX / "low_contention_tc4_batch_20260722.txt"
MAPPING_READY = DATA / "low_contention_tc4_receipt_mapping_ready.json"
BATCH_CERTIFICATE = DATA / "low_contention_tc4_batch_certificate.json"
EXPECTED_ROUTES = 22


def validate_tc3_seal() -> dict:
    value = lane.read_json(REQUIRED_TC3_SEAL)
    if (
        value.get("status") != "sealed_23_of_23_exact_candidates_receipt_mapped"
        or int(value.get("routeCount", -1)) != 23
        or int(value.get("distinctCandidateHashes", -1)) != 23
        or int(value.get("distinctTargetPairs", -1)) != 23
        or value.get("coefficientMaterialIncluded") is not False
    ):
        raise lane.GuardFailure("required tc3 receipt seal is absent or changed")
    return lane.artifact(REQUIRED_TC3_SEAL)


def guard(
    connection: sqlite3.Connection,
    route: dict,
    receipt_hashes: set[str],
    receipt_pairs: set[tuple[str, int]],
    candidate_hash: str | None = None,
) -> dict:
    source, target = route["source"], route["target"]
    pair = (str(target["label"]), int(target["r"]))
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
        raise lane.GuardFailure("tc4 source reconstruction anchor changed")
    if connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
    ).fetchone() is not None:
        raise lane.GuardFailure("tc4 target is baseline")
    if connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", pair
    ).fetchone() is not None:
        raise lane.GuardFailure("tc4 target is already locally verified")
    state = connection.execute(
        "SELECT team_count,discovered,minimum_disc_abs,generated_at "
        "FROM targets WHERE label=? AND r=?", pair
    ).fetchone()
    if state is None or int(state["team_count"]) != 4 or int(state["discovered"] or 0) != 1:
        raise lane.GuardFailure("target is not a discovered tc4 pair in the snapshot")
    if pair in receipt_pairs:
        raise lane.GuardFailure("tc4 target is receipt-covered")
    if candidate_hash is not None:
        if candidate_hash in receipt_hashes:
            raise lane.GuardFailure("tc4 candidate hash is receipt-covered")
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (candidate_hash,),
        ).fetchone() is not None:
            raise lane.GuardFailure("tc4 candidate hash is already in the ledger")
    return {
        "sourceAcceptedScoreable": True,
        "sourceReconstructionHashPinned": True,
        "sourceAnchorFreshUntested": True,
        "targetPair": f"{pair[0]}/r{pair[1]}",
        "targetTeamCount": 4,
        "targetDiscovered": True,
        "targetMinimumDiscAbs": str(state["minimum_disc_abs"]),
        "targetGeneratedAt": str(state["generated_at"]),
        "targetNotBaseline": True,
        "targetNotLocallyVerified": True,
        "targetNotReceiptCovered": True,
        "candidateNovel": candidate_hash is not None,
    }


def write_postflight(
    route: dict, digest: str, checked: dict, receipt_audit: dict, snapshot: dict
) -> Path:
    paths = lane.planned_paths(route)
    source, target = route["source"], route["target"]
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
            "teamCount": 4,
            "discovered": True,
            "minimumDiscAbs": checked["targetMinimumDiscAbs"],
            "generatedAt": checked["targetGeneratedAt"],
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
        "batchTargetSnapshot": snapshot,
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
    snapshot: dict,
    manifest: Path,
    offline: dict,
) -> Path:
    paths = lane.planned_paths(route)
    value = {
        "schemaVersion": "low-contention-higher-tc-stage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_novel_tc4_staged_offline_dry_run_not_submitted",
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
            "irreducible": True,
            "primitiveMonicDegree24": True,
            "exactSquarefreeSingleOrbitCertificate": True,
            "exactTargetAssignment": True,
            "knownLedgerHash": False,
            "receiptHash": False,
        },
        "targetGuard": checked,
        "receiptExclusion": receipt_audit,
        "batchTargetSnapshot": snapshot,
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
    snapshot: dict,
) -> tuple[dict, str | None]:
    try:
        lane.ensure_preworker_absence(route)
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
        postflight = write_postflight(route, digest, checked, receipt_audit, snapshot)
        manifest = lane.ensure_manifest(route, line, digest)
        offline = exact.offline_manifest_check(manifest, [digest], receipt_hashes)
        stage = write_stage(
            route, digest, checked, receipt_audit, snapshot, manifest, offline
        )
        return ({
            "routeId": route["routeId"],
            "status": "certified_exact_tc4_staged_offline_not_submitted",
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
            "reason": str(exc)[:500],
            "workersLaunched": int(lane.planned_paths(route)["result"].exists()),
        }, None)


def write_mapping_ready(
    reports: list[dict], manifest: Path, offline: dict
) -> dict:
    successes = [row for row in reports if row["status"].startswith("certified_exact_")]
    mappings = [
        {
            "manifestPosition": index,
            "routeId": row["routeId"],
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
        "schemaVersion": "low-contention-tc4-receipt-mapping-ready-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_receipt_mapping_after_submission",
        "routeCertificate": lane.artifact(ROUTE_CERTIFICATE),
        "combinedManifest": {
            **lane.artifact(manifest),
            "bytes": manifest.stat().st_size,
            "polynomials": len(successes),
        },
        "combinedOfflineDryRun": offline,
        "routeCount": len(successes),
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
        raise lane.GuardFailure("tc4 combined batch artifact already exists")
    certificate = lane.read_json(ROUTE_CERTIFICATE)
    routes = certificate.get("runbooks") or []
    if (
        certificate.get("scope") != "deterministic_tc4_fresh_untested_anchors"
        or certificate.get("status") != "certified_light_only_runbooks_ready"
        or len(routes) != EXPECTED_ROUTES
        or str(routes[0].get("routeId"))
        != "hc4_001_24T6195_r12_to_24T6237_r4"
        or any(int(row["target"]["teamCountAtSeal"]) != 4 for row in routes)
        or len({str(row["routeId"]) for row in routes}) != EXPECTED_ROUTES
    ):
        raise lane.GuardFailure("tc4 runbook frontier changed or is not ready")

    with lane.execution_lock():
        tc3_seal = validate_tc3_seal()
        with lane.connect_ro() as connection:
            receipt_hashes, receipt_pairs, receipt_audit = lane.receipt_snapshot(connection)
            snapshot = exact.target_snapshot(connection)
        for route in routes:
            lane.ensure_preworker_absence(route)
            with lane.connect_ro() as connection:
                guard(connection, route, receipt_hashes, receipt_pairs)

        reports: list[dict] = []
        lines: list[str] = []
        hashes: list[str] = []
        for index, route in enumerate(routes, start=1):
            report, line = run_route(
                route, receipt_hashes, receipt_pairs, receipt_audit, snapshot
            )
            reports.append(report)
            if line is not None:
                lines.append(line)
                hashes.append(str(report["candidateSha256"]))
            print(json.dumps({
                "route": index,
                "routeId": route["routeId"],
                "status": report["status"],
                "successesSoFar": len(lines),
            }, sort_keys=True), flush=True)

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
            mapping_ready = write_mapping_ready(
                reports, BATCH_MANIFEST, combined_offline
            )
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
        projected = Fraction(len(lines), 16)
        batch_certificate = {
            "schemaVersion": "low-contention-tc4-batch-v1",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "status": (
                "certified_all_tc4_staged_offline_not_submitted"
                if len(lines) == EXPECTED_ROUTES
                else "completed_with_fail_closed_tc4_skips_not_submitted"
            ),
            "routeOrder": [route["routeId"] for route in routes],
            "exclusiveSequentialExecution": True,
            "maximumConcurrentSageGapWorkers": 1,
            "coordinatorHeavySlotLease": "root_authorized_batch_exclusive_slot",
            "routeCertificate": lane.artifact(ROUTE_CERTIFICATE),
            "requiredTc3ReceiptSeal": tc3_seal,
            "receiptExclusion": receipt_audit,
            "targetSnapshot": snapshot,
            "routes": reports,
            "successes": len(lines),
            "failClosedSkips": EXPECTED_ROUTES - len(lines),
            "projectedMarginalScoreExact": (
                str(projected.numerator)
                if projected.denominator == 1
                else f"{projected.numerator}/{projected.denominator}"
            ),
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
                "sageWorkersLaunched": sum(
                    int(row["workersLaunched"]) for row in reports
                ),
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
        "projectedMarginalScoreExact": batch_certificate["projectedMarginalScoreExact"],
        "combinedManifest": (
            lane.relative(BATCH_MANIFEST) if BATCH_MANIFEST.exists() else None
        ),
        "receiptMappingReady": (
            lane.relative(MAPPING_READY) if MAPPING_READY.exists() else None
        ),
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
