#!/usr/bin/env python3
"""Seal a current-boundary authorization to resume exactly tc10 routes 2-3."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import prepare_current_tc7_frontier as receipt_helper
import run_deterministic_frontier_coordinator as coordinator


ROOT = Path(__file__).resolve().parent
PRIOR_ROUTE_CERT = ROOT / "data/low_contention_tc10_current_routes_certificate.json"
ROUTE_CERT = ROOT / "data/low_contention_tc10_current_routes_certificate_v2.json"
CHECKPOINT = ROOT / "data/low_contention_tc10_20260722_checkpoint.json"
OUTPUT = ROOT / "data/low_contention_tc10_routes2_3_resume_authorization.json"
BATCH = "low_contention_tc10_20260722"
SAFE_RECEIPT = "sub_aed2e90f4b804427b2d0f1e497e042ae"
CONDITIONAL_RESULTS = (
    (
        ROOT / "data/gold_conditional_factor_0001_result.jsonl",
        ROOT / "data/gold_profile_backfill_20260722_batch1/conditional_factor_0001_exact_miss_certificate.json",
        "24T13074/r0",
    ),
    (
        ROOT / "data/gold_conditional_factor_0002_result.jsonl",
        ROOT / "data/gold_profile_backfill_20260722_batch1/conditional_factor_0002_exact_miss_certificate.json",
        "24T15251/r20",
    ),
    (
        ROOT / "data/gold_conditional_factor_0003_result.jsonl",
        ROOT / "data/gold_conditional_top10_conditional_factor_0003_stage.json",
        "24T10482/r16",
    ),
)


def main() -> int:
    if OUTPUT.exists():
        raise coordinator.GuardFailure(f"refusing to overwrite {OUTPUT}")
    config = coordinator.Config(
        root=ROOT,
        data=ROOT / "data",
        outbox=ROOT / "outbox",
        receipts=ROOT / "receipts",
        database=ROOT / "data/ledger.sqlite3",
        certificate=ROUTE_CERT,
        batch_name=BATCH,
        reservations=(),
    )
    certificate, routes = coordinator.validate_certificate(config)
    if (
        len(routes) != 13
        or [int(route["target"]["teamCountAtSeal"]) for route in routes] != [10] * 13
        or [int(route["priorityRank"]) for route in routes] != list(range(1, 14))
    ):
        raise coordinator.GuardFailure("tc10 route order changed")
    checkpoint = coordinator.read_json(CHECKPOINT)
    if (
        checkpoint.get("schemaVersion") != "deterministic-frontier-checkpoint-v1"
        or checkpoint.get("status") != "partial_resume_checkpoint_not_submitted"
        or checkpoint.get("batchName") != BATCH
        or checkpoint.get("successes") != 1
        or str((checkpoint.get("routeCertificate") or {}).get("sha256"))
        != coordinator.sha256_path(PRIOR_ROUTE_CERT)
        or checkpoint.get("routeOrder") != [route["routeId"] for route in routes]
    ):
        raise coordinator.GuardFailure("tc10 one-route checkpoint is not intact")
    reports = checkpoint.get("reports") or []
    if (
        len(reports) != 13
        or reports[0].get("status") != "certified_exact_staged_offline_not_submitted"
        or any(
            report.get("status") != "pending_worker_budget_resume_later"
            for report in reports[1:]
        )
    ):
        raise coordinator.GuardFailure("tc10 checkpoint is not paused after route 1")

    with closing(coordinator.connect_ro(config)) as connection:
        boundary = coordinator.boundary_snapshot(connection)
        coordinator.validate_boundary(certificate, boundary)
    exclusions = coordinator.exclusion_snapshot(config)
    _outbox_hashes, _outbox_pairs, _outbox_audit, pairs_by_hash = (
        coordinator.outbox_snapshot(config)
    )
    safe_receipt = receipt_helper.receipt_manifest_audit(
        config, SAFE_RECEIPT, 1, exclusions, pairs_by_hash
    )

    route1_line, route1_digest, _route1_row = coordinator.validate_result(
        config, routes[0]
    )
    with closing(coordinator.connect_ro(config)) as connection:
        route1_guard = coordinator.guard_route(
            connection, routes[0], exclusions, route1_digest
        )
        selected_guards = [
            coordinator.guard_route(connection, route, exclusions)
            for route in routes[1:3]
        ]
    route1_paths = coordinator.route_paths(config, routes[0])
    if any(
        not route1_paths[key].exists()
        for key in ("result", "postflight", "individualManifest", "stageCertificate")
    ):
        raise coordinator.GuardFailure("tc10 route 1 staged artifacts are incomplete")
    partial_rows = [
        line
        for line in config.partial_manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if partial_rows != [route1_line]:
        raise coordinator.GuardFailure("tc10 partial manifest differs from route 1")
    for route in routes[1:3]:
        paths = coordinator.route_paths(config, route)
        if any(
            paths[key].exists()
            for key in (
                "result",
                "resultTemporary",
                "postflight",
                "individualManifest",
                "stageCertificate",
            )
        ):
            raise coordinator.GuardFailure(
                f"authorized pending route already has an artifact: {route['routeId']}"
            )

    conditional_audit = []
    for result_path, cert_path, expected_pair in CONDITIONAL_RESULTS:
        rows = [
            json.loads(line)
            for line in result_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        miss = coordinator.read_json(cert_path)
        if len(rows) != 1 or rows[0].get("status") != "certified":
            raise coordinator.GuardFailure("conditional result is not one exact row")
        realized = f"{rows[0].get('targetLabel')}/r{int(rows[0].get('targetR', -1))}"
        miss_status = str(miss.get("status"))
        if (
            realized != expected_pair
            or "miss" not in miss_status
            or miss.get("manifest") is not None
            and miss_status == "exact_non_gold_miss_no_manifest"
        ):
            raise coordinator.GuardFailure("conditional miss artifact changed")
        conditional_audit.append({
            "result": coordinator.artifact(config, result_path),
            "missCertificate": coordinator.artifact(config, cert_path),
            "realizedPair": realized,
            "status": miss_status,
            "manifestCreated": False,
        })

    value = {
        "schemaVersion": "tc10-current-resume-routes2-3-authorization-v1",
        "createdAt": coordinator.now(),
        "status": "certified_current_boundary_exactly_two_tc10_workers_authorized",
        "scope": "resume_existing_tc10_checkpoint_through_routes_2_and_3_only",
        "routeCertificate": coordinator.artifact(config, ROUTE_CERT),
        "priorRouteCertificate": coordinator.artifact(config, PRIOR_ROUTE_CERT),
        "priorCheckpoint": coordinator.artifact(config, CHECKPOINT),
        "priorPartialManifest": {
            **coordinator.artifact(config, config.partial_manifest),
            "polynomials": 1,
        },
        "boundary": boundary,
        "currentExclusionAudit": exclusions["audit"],
        "namedSafeGoldReceiptAudit": safe_receipt,
        "conditionalMissArtifactAudit": conditional_audit,
        "existingRoute1Audit": {
            "routeId": routes[0]["routeId"],
            "targetPair": route1_guard["targetPair"],
            "candidateSha256": route1_digest,
            "status": "current_exact_checkpoint_staged_and_resumable",
        },
        "authorizedRoutes": [
            {
                "priorityRank": int(route["priorityRank"]),
                "routeId": route["routeId"],
                "targetPair": guard["targetPair"],
                "teamCount": guard["targetTeamCount"],
                "status": "ready_for_one_serial_exact_worker",
            }
            for route, guard in zip(routes[1:3], selected_guards)
        ],
        "execution": {
            "argv": [
                "python3",
                "run_deterministic_frontier_coordinator.py",
                "--certificate",
                "data/low_contention_tc10_current_routes_certificate_v2.json",
                "--batch-name",
                BATCH,
                "--max-new-workers",
                "2",
                "--execute",
            ],
            "maximumNewWorkers": 2,
            "expectedCheckpointSuccessesAfterRun": 3,
            "mustPauseBeforePriorityRank": 4,
        },
        "checks": {
            "originalRouteCertificateAndBoundaryStillValid": True,
            "checkpointPausedAfterExactlyOneSuccess": True,
            "route1ExactCheckpointStillCurrentAndNovel": True,
            "routes2And3CurrentReadyNovel": True,
            "routes4Through13NotAuthorizedThisInvocation": True,
            "safeGoldReceiptAed2ManifestHashAndPairExcluded": True,
            "allCurrentReceiptsAndOutboxesScanned": True,
            "conditional0001Through0003ExactMissArtifactsPinned": True,
            "noConditionalMissManifestPresent": True,
            "oneSharedHeavyWorkerLockRequired": True,
            "coefficientAndCredentialPayloadOmitted": True,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "heavyWorkersLaunched": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    coordinator.write_json(OUTPUT, value)
    print(json.dumps({
        "status": value["status"],
        "authorization": coordinator.artifact(config, OUTPUT),
        "priorSuccesses": 1,
        "authorizedNewWorkers": 2,
        "expectedTotalSuccesses": 3,
        "namedReceiptRowsExcluded": safe_receipt["exactHashesExcluded"],
        "conditionalMissArtifacts": len(conditional_audit),
        "networkCalls": 0,
        "submissionCalls": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        coordinator.GuardFailure,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {coordinator.redact(str(exc))}")
        raise SystemExit(1)
