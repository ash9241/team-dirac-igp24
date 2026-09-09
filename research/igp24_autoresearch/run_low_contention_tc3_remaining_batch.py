#!/usr/bin/env python3
"""Run hc3_002-hc3_023 sequentially under one sealed local snapshot.

This program has no network/submission path.  It holds the shared exclusive
lock for one receipt census and all synchronous Sage workers, then emits exact
per-route postflights/stages and one combined offline-validated manifest.
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
ROUTE_CERTIFICATE = DATA / "low_contention_higher_tc_routes_certificate.json"
REQUIRED_RECEIPT = lane.RECEIPTS / "sub_f7e75a17e20141aaab5e6e2e93fa1eb7.json"
BATCH_MANIFEST = OUTBOX / "low_contention_tc3_remaining_batch_20260722.txt"
BATCH_CERTIFICATE = DATA / "low_contention_tc3_remaining_batch_certificate.json"


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
        raise lane.GuardFailure("tc3 source anchor changed or is not accepted-scoreable")
    if connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
    ).fetchone() is not None:
        raise lane.GuardFailure("tc3 target is baseline")
    if connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", pair
    ).fetchone() is not None:
        raise lane.GuardFailure("tc3 target is already locally verified")
    state = connection.execute(
        "SELECT team_count,discovered,minimum_disc_abs,generated_at "
        "FROM targets WHERE label=? AND r=?", pair
    ).fetchone()
    if state is None or int(state["team_count"]) != 3 or int(state["discovered"] or 0) != 1:
        raise lane.GuardFailure("target is not a discovered tc3 pair in the sealed snapshot")
    if pair in receipt_pairs:
        raise lane.GuardFailure("tc3 target is receipt-covered")
    if candidate_hash is not None:
        if candidate_hash in receipt_hashes:
            raise lane.GuardFailure("tc3 candidate hash is receipt-covered")
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (candidate_hash,),
        ).fetchone() is not None:
            raise lane.GuardFailure("tc3 candidate hash is already in the ledger")
    return {
        "sourceAcceptedScoreable": True,
        "sourceAnchorFreshUntested": True,
        "targetPair": f"{pair[0]}/r{pair[1]}",
        "targetTeamCount": 3,
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
            "label": source["label"], "r": source["r"],
            "coefficientSha256": source["coefficientSha256"],
            "ledgerStatus": "accepted_scoreable",
        },
        "target": {
            "label": target["label"], "r": target["r"], "teamCount": 3,
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
        "status": "certified_exact_novel_tc3_staged_offline_dry_run_not_submitted",
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
            "heavyWorkersLaunched": 1, "networkCalls": 0,
            "ledgerWrites": 0, "submissionCalls": 0,
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
            list(route["heavyCommand"]), cwd=ROOT, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            tail = completed.stderr.strip().splitlines()[-1:] or ["no diagnostic"]
            raise lane.GuardFailure(f"heavy worker failed: {tail[0][:240]}")
        line, digest, _row = exact.validate_worker_result(route)
        with lane.connect_ro() as connection:
            checked = guard(
                connection, route, receipt_hashes, receipt_pairs, digest
            )
        postflight = write_postflight(
            route, digest, checked, receipt_audit, snapshot
        )
        manifest = lane.ensure_manifest(route, line, digest)
        offline = exact.offline_manifest_check(manifest, [digest], receipt_hashes)
        stage = write_stage(
            route, digest, checked, receipt_audit, snapshot, manifest, offline
        )
        return ({
            "routeId": route["routeId"],
            "status": "certified_exact_tc3_staged_offline_not_submitted",
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


def main() -> int:
    if BATCH_MANIFEST.exists() or BATCH_CERTIFICATE.exists():
        raise lane.GuardFailure("tc3 combined batch artifact already exists")
    certificate = lane.read_json(ROUTE_CERTIFICATE)
    all_routes = certificate.get("runbooks") or []
    if (
        certificate.get("scope") != "deterministic_tc3_fresh_untested_anchors"
        or certificate.get("status") != "certified_light_only_runbooks_ready"
        or len(all_routes) != 23
        or not all_routes[0]["routeId"].startswith("hc3_001_")
    ):
        raise lane.GuardFailure("tc3 runbook frontier changed or is not ready")
    routes = all_routes[1:]
    if len(routes) != 22:
        raise lane.GuardFailure("expected twenty-two remaining tc3 routes")

    with lane.execution_lock():
        newest_hashes = exact.required_receipt_hashes(REQUIRED_RECEIPT)
        with lane.connect_ro() as connection:
            receipt_hashes, receipt_pairs, receipt_audit = lane.receipt_snapshot(connection)
            snapshot = exact.target_snapshot(connection)
        if not newest_hashes.issubset(receipt_hashes):
            raise lane.GuardFailure("receipt census omitted committed hc3_001")
        for route in routes:
            lane.ensure_preworker_absence(route)
            with lane.connect_ro() as connection:
                guard(connection, route, receipt_hashes, receipt_pairs)

        reports, lines, hashes = [], [], []
        for route in routes:
            report, line = run_route(
                route, receipt_hashes, receipt_pairs, receipt_audit, snapshot
            )
            reports.append(report)
            if line is not None:
                lines.append(line)
                hashes.append(str(report["candidateSha256"]))
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
        else:
            combined_offline = {
                "commit": False, "networkCalls": 0, "submissionCalls": 0,
                "polynomials": 0, "knownLocalHashes": 0, "receiptHashes": 0,
            }
            manifest_artifact = None
        projected = Fraction(len(lines), 8)
        batch_certificate = {
            "schemaVersion": "low-contention-tc3-remaining-batch-v1",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "status": (
                "certified_all_remaining_tc3_staged_offline_not_submitted"
                if len(lines) == 22
                else "completed_with_fail_closed_tc3_skips_not_submitted"
            ),
            "routeOrder": [route["routeId"] for route in routes],
            "exclusiveSequentialExecution": True,
            "maximumConcurrentSageGapWorkers": 1,
            "coordinatorHeavySlotLease": "root_authorized_batch_exclusive_slot",
            "routeCertificate": lane.artifact(ROUTE_CERTIFICATE),
            "requiredNewestReceipt": lane.artifact(REQUIRED_RECEIPT),
            "receiptExclusion": receipt_audit,
            "targetSnapshot": snapshot,
            "routes": reports,
            "successes": len(lines),
            "failClosedSkips": 22 - len(lines),
            "projectedMarginalScoreExact": (
                str(projected.numerator) if projected.denominator == 1
                else f"{projected.numerator}/{projected.denominator}"
            ),
            "combinedManifest": manifest_artifact,
            "combinedOfflineDryRun": combined_offline,
            "submissionAuthorized": False,
            "coefficientMaterialIncluded": False,
            "credentialMaterialIncluded": False,
            "sideEffects": {
                "sageWorkersLaunched": sum(int(row["workersLaunched"]) for row in reports),
                "networkCalls": 0, "ledgerWrites": 0, "submissionCalls": 0,
            },
        }
        lane.exclusive_json(BATCH_CERTIFICATE, batch_certificate)
    print(json.dumps({
        "status": batch_certificate["status"],
        "successes": batch_certificate["successes"],
        "failClosedSkips": batch_certificate["failClosedSkips"],
        "projectedMarginalScoreExact": batch_certificate["projectedMarginalScoreExact"],
        "combinedManifest": lane.relative(BATCH_MANIFEST) if BATCH_MANIFEST.exists() else None,
        "combinedCertificate": lane.relative(BATCH_CERTIFICATE),
        "networkCalls": 0, "submissionCalls": 0,
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
