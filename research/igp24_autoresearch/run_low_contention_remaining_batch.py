#!/usr/bin/env python3
"""Execute the sealed six-route low-contention tail under one light snapshot.

This program performs no network calls and has no submission path.  It takes
one exact receipt census, holds the shared low-contention execution lock for
its whole lifetime, and invokes each Sage worker synchronously.  Every exact
result receives a coefficient-free postflight and stage certificate plus an
individual manifest.  Successful rows are also collected into one combined
offline-validated manifest.  Route failures are recorded and skipped closed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import run_low_contention_sequential as lane
import sair_api
import stage_single_exact_census as exact_census


ROOT = lane.ROOT
DATA = lane.DATA
OUTBOX = lane.OUTBOX
REQUIRED_RECEIPT = lane.RECEIPTS / "sub_fdaf614deca84c3fb06e00672771f107.json"
BATCH_MANIFEST = OUTBOX / "low_contention_remaining_tc1_batch_20260722.txt"
BATCH_CERTIFICATE = DATA / "low_contention_remaining_tc1_batch_certificate.json"
ROUTE_IDS = (
    "lc07_24T18612_r14_to_24T18869_r8",
    "lc11_24T14132_r24_to_24T14327_r24",
    "lc03_24T17157_r18_to_24T17207_r12",
    "lc06_24T10443_r24_to_24T10829_r24",
    "lc05_24T5424_r24_to_24T5432_r24",
    "lc09_24T13861_r24_to_24T13188_r24",
)


def required_receipt_hashes(path: Path) -> set[str]:
    receipt = lane.read_json(path)
    manifest = Path(str(receipt.get("manifest") or "")).expanduser().resolve()
    if (
        not manifest.is_file()
        or lane.sha256_path(manifest) != str(receipt.get("manifestHash"))
        or int(receipt.get("knownLocalHashes", -1)) != 0
        or int(receipt.get("polynomials", -1)) != 1
    ):
        raise lane.GuardFailure("required newest receipt is absent or not intact")
    lines, hashes = sair_api.validated_manifest(manifest)
    if len(lines) != 1 or len(hashes) != 1:
        raise lane.GuardFailure("required newest receipt is not a one-row exact receipt")
    return set(hashes)


def target_snapshot(connection: sqlite3.Connection) -> dict:
    row = connection.execute(
        "SELECT COUNT(*) AS pairs,COUNT(DISTINCT label) AS labels,"
        "MIN(generated_at) AS generated_min,MAX(generated_at) AS generated_max "
        "FROM targets"
    ).fetchone()
    if row is None or int(row["pairs"]) != 165_836 or int(row["labels"]) != 25_000:
        raise lane.GuardFailure("target snapshot is incomplete")
    return {
        "labels": int(row["labels"]),
        "pairs": int(row["pairs"]),
        "generatedAtMin": str(row["generated_min"]),
        "generatedAtMax": str(row["generated_max"]),
    }


def validate_worker_result(route: dict) -> tuple[str, str, dict]:
    result_path = lane.planned_paths(route)["result"]
    row = lane.one_jsonl(result_path)
    source, target = route["source"], route["target"]
    if (
        row.get("status") != "certified"
        or int(row.get("workerExitCode", -1)) != 0
        or str(row.get("sourceSubmissionId")) != str(source["submissionId"])
        or int(row.get("sourcePolynomialIndex", -1)) != int(source["polynomialIndex"])
        or str(row.get("sourceCoefficientSha256")) != str(source["coefficientSha256"])
        or str(row.get("sourceLabel")) != str(source["label"])
        or int(row.get("sourceR", -1)) != int(source["r"])
        or str(row.get("targetLabel")) != str(target["label"])
        or int(row.get("targetR", -1)) != int(target["r"])
    ):
        raise lane.GuardFailure("worker result differs from the sealed exact route")
    line = exact_census.canonical_polynomial_line(row.get("coefficientLine"))
    if line is None:
        raise lane.GuardFailure("worker candidate is not canonical primitive monic degree 24")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if (
        digest != str(row.get("coefficientSha256"))
        or int(row.get("coefficientBytes", -1)) != len(line.encode("ascii"))
    ):
        raise lane.GuardFailure("worker candidate hash/byte count mismatch")
    orbit = row.get("orbitCertificate") or {}
    actual = [int(value) for value in orbit.get("actualDegrees") or []]
    expected = [int(value) for value in orbit.get("expectedDegrees") or []]
    exponents = [int(value) for value in orbit.get("exponents") or []]
    orbit_targets = row.get("orbitTargets") or []
    if (
        not actual
        or actual != expected
        or actual.count(24) != 1
        or len(exponents) != len(actual)
        or any(value != 1 for value in exponents)
        or len(orbit_targets) != 1
        or str(orbit_targets[0].get("targetLabel")) != str(target["label"])
        or int(orbit_targets[0].get("orbitIndex", -1))
        != int(route["exactAction"]["orbitIndex"])
    ):
        raise lane.GuardFailure("worker orbit/target certificate is not exact squarefree")
    # The pinned pair_sum_one worker sets status=certified only after Sage's
    # is_irreducible(), degree, monicity, and exact real-root checks all pass.
    return line, digest, row


def offline_manifest_check(
    manifest: Path,
    expected_hashes: list[str],
    receipt_hashes: set[str],
) -> dict:
    lines, hashes = sair_api.validated_manifest(manifest)
    if hashes != expected_hashes or len(lines) != len(expected_hashes):
        raise lane.GuardFailure("offline manifest hashes differ from exact staged candidates")
    if len(set(hashes)) != len(hashes) or any(value in receipt_hashes for value in hashes):
        raise lane.GuardFailure("offline manifest contains a duplicate/receipt hash")
    with lane.connect_ro() as connection:
        known = {
            str(row[0])
            for digest in hashes
            for row in connection.execute(
                "SELECT coefficient_hash FROM polynomials WHERE coefficient_hash=?",
                (digest,),
            )
        }
    if known:
        raise lane.GuardFailure("offline manifest contains a ledger-known hash")
    return {
        "commit": False,
        "networkCalls": 0,
        "submissionCalls": 0,
        "polynomials": len(lines),
        "bytes": manifest.stat().st_size,
        "knownLocalHashes": 0,
        "receiptHashes": 0,
        "manifestHash": lane.sha256_path(manifest),
    }


def write_postflight(
    route: dict,
    digest: str,
    guard: dict,
    receipt_audit: dict,
    snapshot: dict,
) -> Path:
    paths = lane.planned_paths(route)
    target = route["target"]
    value = {
        "schemaVersion": "low-contention-pair-route-postflight-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_novel_live_not_staged_not_submitted",
        "routeCertificate": lane.artifact(lane.DEFAULT_CERTIFICATE),
        "routeId": route["routeId"],
        "result": lane.artifact(paths["result"]),
        "source": {
            "submissionId": route["source"]["submissionId"],
            "polynomialIndex": route["source"]["polynomialIndex"],
            "label": route["source"]["label"],
            "r": route["source"]["r"],
            "coefficientSha256": route["source"]["coefficientSha256"],
            "ledgerStatus": "accepted_scoreable",
        },
        "target": {
            "label": target["label"],
            "r": target["r"],
            "teamCount": 1,
            "discovered": True,
            "minimumDiscAbs": guard["targetMinimumDiscAbs"],
            "generatedAt": guard["targetGeneratedAt"],
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
    plan_path: Path,
    route: dict,
    digest: str,
    guard: dict,
    receipt_audit: dict,
    snapshot: dict,
    manifest: Path,
    offline: dict,
) -> Path:
    paths = lane.planned_paths(route)
    value = {
        "schemaVersion": "low-contention-sequential-stage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_novel_tc1_staged_offline_dry_run_not_submitted",
        "routeId": route["routeId"],
        "plan": lane.artifact(plan_path),
        "routeCertificate": lane.artifact(lane.DEFAULT_CERTIFICATE),
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
        "targetGuard": guard,
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
    plan_path: Path,
    route: dict,
    receipt_hashes: set[str],
    receipt_pairs: set[tuple[str, int]],
    receipt_audit: dict,
    snapshot: dict,
) -> tuple[dict, str] | tuple[dict, None]:
    route_id = str(route["routeId"])
    try:
        lane.ensure_preworker_absence(route)
        with lane.connect_ro() as connection:
            before = lane.local_guard(
                connection, route, receipt_hashes, receipt_pairs, None
            )
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
        line, digest, _row = validate_worker_result(route)
        with lane.connect_ro() as connection:
            after = lane.local_guard(
                connection, route, receipt_hashes, receipt_pairs, digest
            )
        postflight = write_postflight(route, digest, after, receipt_audit, snapshot)
        manifest = lane.ensure_manifest(route, line, digest)
        offline = offline_manifest_check(manifest, [digest], receipt_hashes)
        stage = write_stage(
            plan_path, route, digest, after, receipt_audit, snapshot, manifest, offline
        )
        return ({
            "routeId": route_id,
            "status": "certified_exact_staged_offline_not_submitted",
            "targetPair": after["targetPair"],
            "candidateSha256": digest,
            "result": lane.artifact(lane.planned_paths(route)["result"]),
            "postflight": lane.artifact(postflight),
            "manifest": lane.artifact(manifest),
            "stageCertificate": lane.artifact(stage),
            "workersLaunched": 1,
        }, line)
    except (
        lane.GuardFailure,
        ValueError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        return ({
            "routeId": route_id,
            "status": "skipped_fail_closed",
            "reason": str(exc)[:500],
            "workersLaunched": int(lane.planned_paths(route)["result"].exists()),
        }, None)


def main() -> int:
    plan_path = lane.DEFAULT_PLAN.resolve()
    _plan, _certificate, planned = lane.validate_plan(plan_path, lane.DEFAULT_CERTIFICATE)
    by_id = {str(route["routeId"]): route for route in planned}
    routes = [by_id[route_id] for route_id in ROUTE_IDS]
    if BATCH_MANIFEST.exists() or BATCH_CERTIFICATE.exists():
        raise lane.GuardFailure("combined batch artifact already exists")

    with lane.execution_lock():
        newest_hashes = required_receipt_hashes(REQUIRED_RECEIPT)
        with lane.connect_ro() as connection:
            receipt_hashes, receipt_pairs, receipt_audit = lane.receipt_snapshot(connection)
            snapshot = target_snapshot(connection)
        if not newest_hashes.issubset(receipt_hashes):
            raise lane.GuardFailure("fresh receipt census omitted the required newest receipt")

        # Preflight every route before launching the first worker.  Per-route
        # guards are repeated immediately before each synchronous worker.
        for route in routes:
            lane.ensure_preworker_absence(route)
            with lane.connect_ro() as connection:
                lane.local_guard(connection, route, receipt_hashes, receipt_pairs, None)

        reports = []
        successful_lines = []
        successful_hashes = []
        for route in routes:
            report, line = run_route(
                plan_path,
                route,
                receipt_hashes,
                receipt_pairs,
                receipt_audit,
                snapshot,
            )
            reports.append(report)
            if line is not None:
                successful_lines.append(line)
                successful_hashes.append(str(report["candidateSha256"]))

        if successful_lines:
            lane.exclusive_text(BATCH_MANIFEST, "\n".join(successful_lines) + "\n")
            combined_offline = offline_manifest_check(
                BATCH_MANIFEST, successful_hashes, receipt_hashes
            )
            manifest_artifact = {
                **lane.artifact(BATCH_MANIFEST),
                "bytes": BATCH_MANIFEST.stat().st_size,
                "polynomials": len(successful_lines),
            }
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

        projected = Fraction(len(successful_lines), 2)
        certificate = {
            "schemaVersion": "low-contention-remaining-tc1-batch-v1",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "status": (
                "certified_all_remaining_routes_staged_offline_not_submitted"
                if len(successful_lines) == len(routes)
                else "completed_with_fail_closed_route_skips_not_submitted"
            ),
            "routeOrder": list(ROUTE_IDS),
            "exclusiveSequentialExecution": True,
            "maximumConcurrentSageGapWorkers": 1,
            "coordinatorHeavySlotLease": "root_authorized_batch_exclusive_slot",
            "plan": lane.artifact(plan_path),
            "routeCertificate": lane.artifact(lane.DEFAULT_CERTIFICATE),
            "requiredNewestReceipt": lane.artifact(REQUIRED_RECEIPT),
            "receiptExclusion": receipt_audit,
            "targetSnapshot": snapshot,
            "routes": reports,
            "successes": len(successful_lines),
            "failClosedSkips": len(routes) - len(successful_lines),
            "projectedMarginalScoreExact": (
                str(projected.numerator)
                if projected.denominator == 1
                else f"{projected.numerator}/{projected.denominator}"
            ),
            "combinedManifest": manifest_artifact,
            "combinedOfflineDryRun": combined_offline,
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
        lane.exclusive_json(BATCH_CERTIFICATE, certificate)

    print(json.dumps({
        "status": certificate["status"],
        "successes": certificate["successes"],
        "failClosedSkips": certificate["failClosedSkips"],
        "projectedMarginalScoreExact": certificate["projectedMarginalScoreExact"],
        "combinedManifest": (
            lane.relative(BATCH_MANIFEST) if BATCH_MANIFEST.exists() else None
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
        lane.GuardFailure,
        ValueError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
