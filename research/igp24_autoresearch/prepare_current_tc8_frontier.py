#!/usr/bin/env python3
"""Rebase the sealed tc8 slice onto current receipts, outboxes, and targets."""

from __future__ import annotations

import copy
import json
from contextlib import closing
from pathlib import Path

import prepare_current_tc7_frontier as receipt_helper
import run_deterministic_frontier_coordinator as coordinator


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data/low_contention_tc7_tc9_routes_certificate.json"
OUTPUT = ROOT / "data/low_contention_tc8_current_routes_certificate.json"
BATCH_NAME = "low_contention_tc8_20260722"
NAMED_RECEIPTS = {
    "sub_f34764affb9f428b93cb5f3577001a73": 9,
    "sub_858b956217294d8cb269f12a098c2eb5": 2,
}


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
    _parent, routes = coordinator.validate_certificate(config)
    team_counts = [int(route["target"]["teamCountAtSeal"]) for route in routes]
    if (
        len(routes) != 30
        or team_counts != [7] * 9 + [8] * 11 + [9] * 10
        or [int(route["priorityRank"]) for route in routes] != list(range(1, 31))
    ):
        raise coordinator.GuardFailure("sealed tc7/tc8/tc9 route order changed")

    selected = copy.deepcopy(routes[9:20])
    if [int(route.get("layerRank", -1)) for route in selected] != list(range(1, 12)):
        raise coordinator.GuardFailure("tc8 layer order changed")
    for priority, route in enumerate(selected, start=1):
        route["parentPriorityRank"] = int(route["priorityRank"])
        route["priorityRank"] = priority

    target_refresh = []
    with closing(coordinator.connect_ro(config)) as connection:
        boundary = coordinator.boundary_snapshot(connection)
        for route in selected:
            target = route["target"]
            pair = (str(target["label"]), int(target["r"]))
            current = connection.execute(
                "SELECT team_count,discovered,minimum_disc_abs,generated_at "
                "FROM targets WHERE label=? AND r=?", pair
            ).fetchone()
            if (
                current is None
                or int(current["team_count"]) != 8
                or int(current["discovered"] or 0) != 1
                or str(current["minimum_disc_abs"])
                != str(target["minimumDiscAbsAtSeal"])
            ):
                raise coordinator.GuardFailure(
                    f"tc8 target score state changed: {coordinator.pair_text(pair)}"
                )
            prior_generated = str(target["generatedAtSeal"])
            target["generatedAtSeal"] = str(current["generated_at"])
            target_refresh.append({
                "targetPair": coordinator.pair_text(pair),
                "priorGeneratedAt": prior_generated,
                "currentGeneratedAt": str(current["generated_at"]),
                "teamCount": 8,
                "discovered": True,
                "minimumDiscAbsUnchanged": True,
            })

    exclusions = coordinator.exclusion_snapshot(config)
    _outbox_hashes, _outbox_pairs, _outbox_audit, pairs_by_hash = (
        coordinator.outbox_snapshot(config)
    )
    with closing(coordinator.connect_ro(config)) as connection:
        route_guards = [
            coordinator.guard_route(connection, route, exclusions)
            for route in selected
        ]
    named = [
        receipt_helper.receipt_manifest_audit(
            config, submission_id, rows, exclusions, pairs_by_hash
        )
        for submission_id, rows in NAMED_RECEIPTS.items()
    ]

    value = {
        "schemaVersion": "low-contention-current-tc8-frontier-v1",
        "createdAt": coordinator.now(),
        "status": "certified_current_receipt_outbox_aware_tc8_runbooks_ready",
        "scope": "current_boundary_tc8_only_eleven_ranked_routes",
        "parentCertificate": coordinator.artifact(config, SOURCE),
        "boundary": boundary,
        "runbooks": selected,
        "currentExclusionAudit": exclusions["audit"],
        "namedReceiptAudit": named,
        "targetTimestampRefresh": target_refresh,
        "routeGuards": route_guards,
        "checks": {
            "parentCertificateValidated": True,
            "selectedElevenRoutesAreExactlyTc8": True,
            "tc7Tc9RoutesExcludedFromDerivative": True,
            "currentBoundaryCaptured": True,
            "targetTeamCountDiscoveryAndMinimumDiscUnchanged": True,
            "targetRefreshTimestampsRebased": True,
            "allCurrentReceiptsScanned": True,
            "allCurrentOutboxesScanned": True,
            "tc7ReceiptManifestHashesAndPairsExcluded": True,
            "twistReceiptManifestHashesAndPairsExcluded": True,
            "allElevenSourcesAcceptedScoreable": True,
            "allElevenTargetsCurrentNovel": True,
            "certificateContainsNoCoefficientPayload": True,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
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
        "routes": len(selected),
        "teamCount": 8,
        "currentReceiptHashes": len(exclusions["receiptHashes"]),
        "currentReceiptPairs": len(exclusions["receiptPairs"]),
        "currentOutboxHashes": len(exclusions["outboxHashes"]),
        "currentOutboxPairs": len(exclusions["outboxPairs"]),
        "namedReceiptRowsExcluded": sum(row["exactHashesExcluded"] for row in named),
        "networkCalls": 0,
        "submissionCalls": 0,
    }, sort_keys=True))
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
