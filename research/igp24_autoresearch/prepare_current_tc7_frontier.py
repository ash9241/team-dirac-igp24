#!/usr/bin/env python3
"""Rebase the sealed tc7 slice onto the current local receipt/target boundary."""

from __future__ import annotations

import copy
import json
from contextlib import closing
from pathlib import Path

import run_deterministic_frontier_coordinator as coordinator


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data/low_contention_tc7_tc9_routes_certificate.json"
OUTPUT = ROOT / "data/low_contention_tc7_current_routes_certificate_v2.json"
BATCH_NAME = "low_contention_tc7_20260722"
NAMED_RECEIPTS = {
    "sub_ebc16254512a411b902ac0212d62f21b": 8,
    "sub_7f6cd8ec78604eaca6fa5a8e373f90e5": 2,
}


def receipt_manifest_audit(
    config: coordinator.Config,
    submission_id: str,
    expected_rows: int,
    exclusions: dict,
    pairs_by_hash: dict[str, set[tuple[str, int]]],
) -> dict:
    path = config.receipts / f"{submission_id}.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    response = receipt.get("response") or {}
    manifest = Path(str(receipt.get("manifest"))).resolve()
    if not manifest.is_relative_to(config.root) or not manifest.is_file():
        raise coordinator.GuardFailure(f"named receipt manifest is absent: {submission_id}")
    if (
        receipt.get("commit") is not True
        or int(receipt.get("polynomials", -1)) != expected_rows
        or str(response.get("submissionId")) != submission_id
        or int(response.get("rejectedCount", 0)) != 0
        or list(response.get("failedPolynomials") or [])
        or coordinator.sha256_path(manifest) != str(receipt.get("manifestHash"))
    ):
        raise coordinator.GuardFailure(f"named receipt is not intact: {submission_id}")
    hashes = set()
    pairs = set()
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        line = coordinator.exact_census.canonical_polynomial_line(raw)
        if line is None:
            if raw.strip():
                raise coordinator.GuardFailure("named receipt manifest is not canonical")
            continue
        digest = coordinator.sha256_bytes(line.encode("ascii"))
        hashes.add(digest)
        pairs.update(pairs_by_hash.get(digest, set()))
    if (
        len(hashes) != expected_rows
        or not hashes.issubset(exclusions["receiptHashes"])
        or len(pairs) != expected_rows
        or not pairs.issubset(exclusions["receiptPairs"])
    ):
        raise coordinator.GuardFailure(
            f"named receipt hashes/pairs are not fully excluded: {submission_id}"
        )
    with closing(coordinator.connect_ro(config)) as connection:
        accepted = int(connection.execute(
            "SELECT COUNT(*) FROM verifications WHERE submission_id=? "
            "AND status='accepted' AND scoreable=1",
            (submission_id,),
        ).fetchone()[0])
    return {
        "submissionId": submission_id,
        "receipt": coordinator.artifact(config, path),
        "manifest": {
            **coordinator.artifact(config, manifest),
            "polynomials": expected_rows,
        },
        "submissionStatusInReceipt": str(response.get("submissionStatus")),
        "acceptedScoreableRowsInLedger": accepted,
        "exactHashesExcluded": len(hashes),
        "exactPairsExcluded": len(pairs),
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
    parent, routes = coordinator.validate_certificate(config)
    team_counts = [int(route["target"]["teamCountAtSeal"]) for route in routes]
    if (
        len(routes) != 30
        or team_counts != [7] * 9 + [8] * 11 + [9] * 10
        or [int(route["priorityRank"]) for route in routes] != list(range(1, 31))
    ):
        raise coordinator.GuardFailure("sealed tc7/tc8/tc9 route order changed")
    selected = copy.deepcopy(routes[:9])
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
                or int(current["team_count"]) != 7
                or int(current["discovered"] or 0) != 1
                or str(current["minimum_disc_abs"])
                != str(target["minimumDiscAbsAtSeal"])
            ):
                raise coordinator.GuardFailure(
                    f"tc7 target score state changed: {coordinator.pair_text(pair)}"
                )
            prior_generated = str(target["generatedAtSeal"])
            target["generatedAtSeal"] = str(current["generated_at"])
            target_refresh.append({
                "targetPair": coordinator.pair_text(pair),
                "priorGeneratedAt": prior_generated,
                "currentGeneratedAt": str(current["generated_at"]),
                "teamCount": 7,
                "discovered": True,
                "minimumDiscAbsUnchanged": True,
            })
    exclusions = coordinator.exclusion_snapshot(config)
    _outbox_hashes, _outbox_pairs, _outbox_audit, pairs_by_hash = (
        coordinator.outbox_snapshot(config)
    )
    route_guards = []
    with closing(coordinator.connect_ro(config)) as connection:
        for route in selected:
            route_guards.append(
                coordinator.guard_route(connection, route, exclusions)
            )
    named = [
        receipt_manifest_audit(
            config, submission_id, rows, exclusions, pairs_by_hash
        )
        for submission_id, rows in NAMED_RECEIPTS.items()
    ]
    value = {
        "schemaVersion": "low-contention-current-tc7-frontier-v1",
        "createdAt": coordinator.now(),
        "status": "certified_current_receipt_outbox_aware_tc7_runbooks_ready",
        "scope": "current_boundary_tc7_only_nine_ranked_routes",
        "parentCertificate": coordinator.artifact(config, SOURCE),
        "boundary": boundary,
        "runbooks": selected,
        "currentExclusionAudit": exclusions["audit"],
        "namedReceiptAudit": named,
        "targetTimestampRefresh": target_refresh,
        "routeGuards": route_guards,
        "checks": {
            "parentCertificateValidated": True,
            "firstNineRoutesAreExactlyTc7": True,
            "tc8Tc9RoutesExcludedFromDerivative": True,
            "currentBoundaryCaptured": True,
            "targetTeamCountDiscoveryAndMinimumDiscUnchanged": True,
            "targetRefreshTimestampsRebased": True,
            "allCurrentReceiptsScanned": True,
            "allCurrentOutboxesScanned": True,
            "ebcManifestHashesAndPairsExcluded": True,
            "queued7f6ManifestHashesAndPairsExcluded": True,
            "allNineSourcesAcceptedScoreable": True,
            "allNineTargetsCurrentNovel": True,
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
        "teamCount": 7,
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
