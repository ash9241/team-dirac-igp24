#!/usr/bin/env python3
"""Rebase the sealed tc10 slice onto current receipts, outboxes, and targets."""

from __future__ import annotations

import copy
import json
from contextlib import closing
from pathlib import Path

import prepare_current_tc7_frontier as receipt_helper
import run_deterministic_frontier_coordinator as coordinator


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data/low_contention_tc10_tc12_routes_certificate.json"
OUTPUT = ROOT / "data/low_contention_tc10_current_routes_certificate.json"
BATCH_NAME = "low_contention_tc10_20260722"
NAMED_RECEIPTS = {
    "sub_f34764affb9f428b93cb5f3577001a73": 9,
    "sub_858b956217294d8cb269f12a098c2eb5": 2,
    "sub_9850254c45164663a2db09657e84d082": 11,
    "sub_c6f6ebb4f625451d9b552a523274b1f6": 9,
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
        len(routes) != 24
        or team_counts != [10] * 13 + [11] * 8 + [12] * 3
        or [int(route["priorityRank"]) for route in routes]
        != list(range(1, 25))
    ):
        raise coordinator.GuardFailure("sealed tc10/tc11/tc12 route order changed")

    selected = copy.deepcopy(routes[:13])
    if [int(route.get("layerRank", -1)) for route in selected] != list(range(1, 14)):
        raise coordinator.GuardFailure("tc10 layer order changed")
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
            owned = connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? "
                "AND scoreable=1 LIMIT 1", pair
            ).fetchone() is not None
            if (
                current is None
                or int(current["team_count"]) != 10
                or int(current["discovered"] or 0) != 1
                or str(current["minimum_disc_abs"])
                != str(target["minimumDiscAbsAtSeal"])
                or owned
            ):
                raise coordinator.GuardFailure(
                    f"tc10 target score/ownership state changed: "
                    f"{coordinator.pair_text(pair)}"
                )
            prior_generated = str(target["generatedAtSeal"])
            target["generatedAtSeal"] = str(current["generated_at"])
            target_refresh.append({
                "targetPair": coordinator.pair_text(pair),
                "priorGeneratedAt": prior_generated,
                "currentGeneratedAt": str(current["generated_at"]),
                "teamCount": 10,
                "discovered": True,
                "locallyOwned": False,
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
        "schemaVersion": "low-contention-current-tc10-frontier-v1",
        "createdAt": coordinator.now(),
        "status": "certified_current_receipt_outbox_aware_tc10_runbooks_ready",
        "scope": "current_boundary_tc10_only_thirteen_ranked_routes",
        "parentCertificate": coordinator.artifact(config, SOURCE),
        "boundary": boundary,
        "runbooks": selected,
        "currentExclusionAudit": exclusions["audit"],
        "namedReceiptAudit": named,
        "targetTimestampRefresh": target_refresh,
        "routeGuards": route_guards,
        "checks": {
            "parentCertificateValidated": True,
            "selectedThirteenRoutesAreExactlyTc10": True,
            "tc11Tc12RoutesExcludedFromDerivative": True,
            "currentBoundaryCaptured": True,
            "targetTeamCountDiscoveryMinimumDiscAndOwnershipUnchanged": True,
            "targetRefreshTimestampsRebased": True,
            "allCurrentReceiptsScanned": True,
            "allCurrentOutboxesScanned": True,
            "tc7ReceiptManifestHashesAndPairsExcluded": True,
            "twistReceiptManifestHashesAndPairsExcluded": True,
            "tc8ReceiptManifestHashesAndPairsExcluded": True,
            "tc9ReceiptManifestHashesAndPairsExcluded": True,
            "allThirteenSourcesAcceptedScoreable": True,
            "allThirteenTargetsCurrentNovel": True,
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
        "teamCount": 10,
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
