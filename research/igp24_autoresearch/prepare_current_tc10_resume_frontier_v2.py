#!/usr/bin/env python3
"""Rebase the tc10 frontier for a checkpoint-compatible current resume."""

from __future__ import annotations

import copy
import json
from contextlib import closing
from pathlib import Path

import prepare_current_tc7_frontier as receipt_helper
import run_deterministic_frontier_coordinator as coordinator


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data/low_contention_tc10_current_routes_certificate.json"
OUTPUT = ROOT / "data/low_contention_tc10_current_routes_certificate_v2.json"
BATCH_NAME = "low_contention_tc10_20260722"
SAFE_RECEIPT = "sub_aed2e90f4b804427b2d0f1e497e042ae"
CONDITIONAL_ARTIFACTS = (
    ROOT / "data/gold_conditional_factor_0001_result.jsonl",
    ROOT / "data/gold_profile_backfill_20260722_batch1/conditional_factor_0001_exact_miss_certificate.json",
    ROOT / "data/gold_conditional_factor_0002_result.jsonl",
    ROOT / "data/gold_profile_backfill_20260722_batch1/conditional_factor_0002_exact_miss_certificate.json",
    ROOT / "data/gold_conditional_factor_0003_result.jsonl",
    ROOT / "data/gold_conditional_top10_conditional_factor_0003_stage.json",
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
        certificate=SOURCE,
        batch_name=BATCH_NAME,
        reservations=(),
    )
    _parent, source_routes = coordinator.validate_certificate(config)
    if (
        len(source_routes) != 13
        or [int(route["target"]["teamCountAtSeal"]) for route in source_routes]
        != [10] * 13
        or [int(route["priorityRank"]) for route in source_routes]
        != list(range(1, 14))
    ):
        raise coordinator.GuardFailure("sealed tc10 route order changed")
    routes = copy.deepcopy(source_routes)

    refresh = []
    with closing(coordinator.connect_ro(config)) as connection:
        boundary = coordinator.boundary_snapshot(connection)
        for route in routes:
            target = route["target"]
            pair = (str(target["label"]), int(target["r"]))
            current = connection.execute(
                "SELECT team_count,discovered,minimum_disc_abs,generated_at "
                "FROM targets WHERE label=? AND r=?", pair
            ).fetchone()
            known = connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", pair
            ).fetchone() is not None
            if (
                current is None
                or int(current["team_count"]) != 10
                or int(current["discovered"] or 0) != 1
                or str(current["minimum_disc_abs"])
                != str(target["minimumDiscAbsAtSeal"])
                or known
            ):
                raise coordinator.GuardFailure(
                    f"tc10 target score/ownership state changed: "
                    f"{coordinator.pair_text(pair)}"
                )
            prior = str(target["generatedAtSeal"])
            target["generatedAtSeal"] = str(current["generated_at"])
            refresh.append({
                "targetPair": coordinator.pair_text(pair),
                "priorGeneratedAt": prior,
                "currentGeneratedAt": str(current["generated_at"]),
                "teamCount": 10,
                "discovered": True,
                "minimumDiscAbsUnchanged": True,
                "locallyKnown": False,
            })

    # Use the real tc10 batch name so its own private checkpoint/manifests are
    # excluded while every unrelated current outbox and receipt is included.
    exclusions = coordinator.exclusion_snapshot(config)
    _outbox_hashes, _outbox_pairs, _outbox_audit, pairs_by_hash = (
        coordinator.outbox_snapshot(config)
    )
    safe_receipt = receipt_helper.receipt_manifest_audit(
        config, SAFE_RECEIPT, 1, exclusions, pairs_by_hash
    )
    with closing(coordinator.connect_ro(config)) as connection:
        route_guards = [
            coordinator.guard_route(connection, route, exclusions) for route in routes
        ]

    conditional_artifacts = []
    for path in CONDITIONAL_ARTIFACTS:
        if not path.is_file():
            raise coordinator.GuardFailure(f"conditional artifact is absent: {path}")
        if path.suffix == ".json":
            value = coordinator.read_json(path)
            status = str(value.get("status"))
            if "miss" not in status:
                raise coordinator.GuardFailure("conditional certificate is not a miss")
        else:
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if len(rows) != 1 or rows[0].get("status") != "certified":
                raise coordinator.GuardFailure("conditional result is not exact isolated")
            status = "certified_exact_result_nongold_not_staged"
        conditional_artifacts.append({
            **coordinator.artifact(config, path),
            "status": status,
        })

    value = {
        "schemaVersion": "low-contention-current-tc10-resume-frontier-v2",
        "createdAt": coordinator.now(),
        "status": "certified_current_receipt_outbox_aware_tc10_resume_runbooks_ready",
        "scope": "current_boundary_tc10_thirteen_routes_checkpoint_resume_compatible",
        "parentCertificate": coordinator.artifact(config, SOURCE),
        "boundary": boundary,
        "runbooks": routes,
        "currentExclusionAudit": exclusions["audit"],
        "namedSafeGoldReceiptAudit": safe_receipt,
        "conditionalMissArtifacts": conditional_artifacts,
        "targetTimestampRefresh": refresh,
        "routeGuards": route_guards,
        "resumeCompatibility": {
            "batchName": BATCH_NAME,
            "routeIdsCommandsAndOutputsUnchanged": True,
            "existingExactRouteResultsMayBeResumedWithoutWorker": True,
        },
        "checks": {
            "parentCertificateValidated": True,
            "allThirteenRoutesRemainExactlyTc10": True,
            "routeOrderCommandsAndOutputsUnchanged": True,
            "currentBoundaryCaptured": True,
            "targetTeamCountDiscoveryMinimumDiscAndOwnershipUnchanged": True,
            "targetRefreshTimestampsRebased": True,
            "allCurrentReceiptsScanned": True,
            "allCurrentOutboxesScannedExceptOwnPrivateTc10CheckpointArtifacts": True,
            "safeGoldReceiptAed2HashAndPairExcluded": True,
            "conditional0001Through0003MissArtifactsPinned": True,
            "allThirteenSourcesAcceptedScoreable": True,
            "allThirteenTargetsCurrentNovel": True,
            "certificateContainsNoCoefficientPayload": True,
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
        "certificate": coordinator.artifact(config, OUTPUT),
        "routes": len(routes),
        "currentReceiptHashes": len(exclusions["receiptHashes"]),
        "currentReceiptPairs": len(exclusions["receiptPairs"]),
        "currentOutboxHashes": len(exclusions["outboxHashes"]),
        "currentOutboxPairs": len(exclusions["outboxPairs"]),
        "safeReceiptRowsExcluded": safe_receipt["exactHashesExcluded"],
        "conditionalArtifactsPinned": len(conditional_artifacts),
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
