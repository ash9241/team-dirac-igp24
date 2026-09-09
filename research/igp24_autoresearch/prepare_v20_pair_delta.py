#!/usr/bin/env python3
"""Light-only two-phase post-v19 pair-delta preparer."""

from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import prepare_v11_pair_delta as helper
import prepare_v19_pair_delta as prior


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
BASE = ROOT / "data/autopilot_pair_delta_20260722_v19"
DEST = ROOT / "data/autopilot_pair_delta_20260722_v20"
GROUP_INPUT = DEST / "group_input.jsonl"
GROUP_SUMMARY = DEST / "group_input_summary.json"
CENSUS_INPUT = DEST / "census_input.jsonl"
INVENTORY = DEST / "exact_source_inventory.json"
PLAN = DEST / "provenance_plan.json"
PREFLIGHT = DEST / "preflight_audit_summary.json"
OUTPUT = DEST / "missing_pair_all.jsonl"

BASE_ROWS = 13_346
BASE_PAIRS = 22_028
BASE_DELTA_ROWS = 7
BASE_DELTA_PAIRS = 8
TARGET_ROWS = 165_836
TARGET_LABELS = 25_000
RECEIPT_ID = "sub_7f6cd8ec78604eaca6fa5a8e373f90e5"
RECEIPT_COUNT = 2

PINS = {
    "prepare_v19_pair_delta.py": "b7c8ce2693591930c79018f7275c48e887baac84232498dbf39076a05d04bde3",
    "data/autopilot_pair_delta_20260722_v19/group_input.jsonl": "ec44a7388be15146edc2c13e870eb3bf7dd5736c982ab5d02e99c00ef79066d1",
    "data/autopilot_pair_delta_20260722_v19/group_input_summary.json": "cc633e277922ca629420b4a67a55ca13504603dd31cd7865b1e51b7e98c1e0a2",
    "data/autopilot_pair_delta_20260722_v19/census_input.jsonl": "fd8057a3c36b8db40d48f3e7c769d4155d152b148f1705e7e5685c47ee23d9e3",
    "data/autopilot_pair_delta_20260722_v19/missing_pair_all.jsonl": "e481679e726be67e386c920725fb6cf026825f10be2306ca14adc5229605c946",
    "data/autopilot_pair_delta_20260722_v19/exact_source_inventory.json": "6b9dcde010fc88295a025046261d93342890ca5822250b8f64c46bd72eb2f052",
    "data/autopilot_pair_delta_20260722_v19/provenance_plan.json": "17b4a63de797d4f9c3c3852d7fdae2f946c49659825584211d8e044c0538b4c0",
    "data/autopilot_pair_delta_20260722_v19/preflight_audit_summary.json": "d31dfe73dd127f5991a3a5b0ced70ffd5c9ec52afdbbf0cb7cd0b30229356ec2",
    f"receipts/{RECEIPT_ID}.json": "d3fe797160de17e68a3c7ca985b98d2b46db944dc73357f511024774bb5c1c8f",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalize-heavy-plan", action="store_true")
    parser.add_argument("--expected-delta-pairs", type=int)
    return parser.parse_args()


def require_pins() -> None:
    for relative, digest in PINS.items():
        path = ROOT / relative
        if not path.is_file() or helper.sha256_path(path) != digest:
            raise ValueError(f"pinned v19 artifact changed: {relative}")


def validate_base() -> tuple[set[tuple[str, int]], list[dict], set[tuple[str, int]]]:
    require_pins()
    group = helper.read_jsonl(BASE / "group_input.jsonl")
    census = helper.read_jsonl(BASE / "census_input.jsonl")
    completed = helper.read_jsonl(BASE / "missing_pair_all.jsonl")
    summary = helper.read_json(BASE / "group_input_summary.json")
    inventory = helper.read_json(BASE / "exact_source_inventory.json")
    plan = helper.read_json(BASE / "provenance_plan.json")
    preflight = helper.read_json(BASE / "preflight_audit_summary.json")
    owned = helper.source_pairs(group)
    selected = helper.source_pairs(census)
    done = {
        (str(row.get("sourceLabel")), int(r))
        for row in completed for r in row.get("sourceR") or []
    }
    inventory_pairs = {
        prior.prior.prior.parsed_pair(value)
        for value in inventory.get("deltaPairs") or []
    }
    if (
        len(group) != BASE_ROWS or len(owned) != BASE_PAIRS
        or len(census) != BASE_ROWS or len(completed) != BASE_DELTA_ROWS
        or len(selected) != BASE_DELTA_PAIRS or selected != done or selected != inventory_pairs
        or sum(len(row.get("routes") or []) for row in completed) != 0
        or any(row.get("status") != "certified" or not helper.certificate_is_exact(row) for row in completed)
        or summary.get("status") != "certified_boundary_confirmed"
        or summary.get("heavyPlanFinalized") is not True
        or int(summary.get("snapshotOwnedPairs", -1)) != BASE_PAIRS
        or int(summary.get("deltaPairs", -1)) != BASE_DELTA_PAIRS
        or inventory.get("status") != "certified"
        or int(inventory.get("deltaPairCount", -1)) != BASE_DELTA_PAIRS
        or set((inventory.get("acceptedAnchorsByPair") or {}))
        != {prior.prior.prior.pair_text(pair) for pair in selected}
        or plan.get("status") != "ready_for_one_heavy_worker"
        or (plan.get("execution") or {}).get("heavyCommandFrozen") is not True
        or preflight.get("status") != "certified_safe_for_one_heavy_worker"
        or not all((preflight.get("checks") or {}).values())
    ):
        raise ValueError("v19 frozen zero-route boundary changed")
    maps = list((plan.get("artifacts") or {}).get("priorMaps") or [])
    if len(maps) != 22:
        raise ValueError("v19 prior chain cardinality changed")
    signatures = set()
    for artifact in maps:
        path = ROOT / artifact["path"]
        if not path.is_file() or helper.sha256_path(path) != artifact["sha256"]:
            raise ValueError(f"prior map changed: {path}")
        for row in helper.read_jsonl(path):
            if row.get("sourceLabel") is not None:
                signatures.update((str(row["sourceLabel"]), int(r)) for r in row.get("sourceR") or [])
    maps.append(helper.relative_artifact(BASE / "missing_pair_all.jsonl"))
    signatures.update(done)
    if not signatures <= owned:
        raise ValueError("prior chain through v19 escapes ownership")
    return owned, maps, signatures


def validate_receipt(connection: sqlite3.Connection) -> dict:
    receipt_path = ROOT / f"receipts/{RECEIPT_ID}.json"
    receipt = helper.read_json(receipt_path)
    hashes = prior.prior.manifest_hashes(receipt, RECEIPT_COUNT)
    submission = connection.execute(
        "SELECT queued_count,verified_count,failed_count,synced_at FROM submissions WHERE submission_id=?",
        (RECEIPT_ID,),
    ).fetchone()
    rows = list(connection.execute(
        "SELECT p.coefficient_hash,v.label,v.r,v.status,v.scoreable,v.scoring_status,v.in_baseline "
        "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
        "WHERE p.submission_id=? ORDER BY p.polynomial_index", (RECEIPT_ID,),
    ))
    if (
        submission is None or tuple(map(int, submission[:3])) != (0, RECEIPT_COUNT, 0)
        or len(rows) != RECEIPT_COUNT or {str(row[0]) for row in rows} != hashes
        or len({(str(row[1]), int(row[2])) for row in rows}) != RECEIPT_COUNT
        or any(str(row[3]) != "accepted" or int(row[4]) != 1 or str(row[5]) != "scoreable" or int(row[6]) != 0 for row in rows)
    ):
        raise ValueError("v17 gold receipt is not exactly verified")
    return {
        "receipt": helper.relative_artifact(receipt_path),
        "manifestSha256": receipt["manifestHash"],
        "verifiedAcceptedScoreable": RECEIPT_COUNT,
        "distinctPairs": RECEIPT_COUNT,
        "syncedAtEpoch": float(submission[3]),
    }


def main() -> int:
    args = parse_args()
    if args.finalize_heavy_plan and args.expected_delta_pairs is None:
        raise ValueError("--expected-delta-pairs required")
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite heavy output: {OUTPUT}")
    base_pairs, prior_maps, prior_signatures = validate_base()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        receipt = validate_receipt(connection)
        baseline = {(str(label), int(r)) for label, r in connection.execute("SELECT label,r FROM baseline_pairs")}
        accepted = {(str(label), int(r)) for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE status='accepted' AND scoreable=1"
        )}
        if not base_pairs <= accepted:
            raise ValueError("ledger regressed behind v19")
        delta = accepted - base_pairs
        if not delta or delta & baseline or delta & prior_signatures:
            raise ValueError("post-v19 delta empty or colliding")
        if args.expected_delta_pairs is not None and len(delta) != args.expected_delta_pairs:
            raise ValueError(f"expected {args.expected_delta_pairs} pairs, found {len(delta)}")
        anchors = prior.prior.prior.accepted_anchors(connection, delta)
        targets = [
            (str(label), int(r), int(tc), bool(d), str(md) if md is not None else None, str(stamp))
            for label, r, tc, d, md, stamp in connection.execute(
                "SELECT label,r,team_count,discovered,minimum_disc_abs,generated_at FROM targets"
            )
        ]
        anchor_rows = int(connection.execute(
            "SELECT COUNT(*) FROM verifications WHERE status='accepted' AND scoreable=1"
        ).fetchone()[0])
        latest_sync = connection.execute("SELECT MAX(synced_at) FROM submissions").fetchone()[0]
    finally:
        connection.close()
    target_map = {(row[0], row[1]): row[2:] for row in targets}
    if len(target_map) != TARGET_ROWS or len({row[0] for row in targets}) != TARGET_LABELS or not delta <= set(target_map):
        raise ValueError("target cache incomplete")
    owned_by_label = {}
    for label, r in accepted:
        owned_by_label.setdefault(label, set()).add(r)
    gold_by_label = {}
    for pair, state in target_map.items():
        if state[0] == 0 and pair not in baseline and pair not in accepted:
            gold_by_label.setdefault(pair[0], set()).add(pair[1])
    labels = sorted(set(owned_by_label) | set(gold_by_label), key=lambda value: int(value[3:]))
    group = [{
        "goldR": sorted(gold_by_label.get(label, set())), "isGoldTarget": label in gold_by_label,
        "isOwnedSource": label in owned_by_label, "label": label,
        "sourceR": sorted(owned_by_label.get(label, set())), "t": int(label[3:]),
    } for label in labels]
    if helper.source_pairs(group) != accepted:
        raise ValueError("v20 group ownership mismatch")
    census = []
    for row in group:
        selected = sorted(r for label, r in delta if label == row["label"])
        census.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if helper.source_pairs(census) != delta:
        raise ValueError("v20 delta isolation mismatch")
    pair_key = prior.prior.prior.pair_key
    pair_text = prior.prior.prior.pair_text
    sorted_delta = sorted(delta, key=pair_key)
    delta_labels = {label for label, _ in delta}
    boundary = {
        "groupInput": helper.relative_artifact(BASE / "group_input.jsonl"),
        "censusInput": helper.relative_artifact(BASE / "census_input.jsonl"),
        "completedCensus": helper.relative_artifact(BASE / "missing_pair_all.jsonl"),
        "preflight": helper.relative_artifact(BASE / "preflight_audit_summary.json"),
    }
    summary = {
        "schemaVersion": "v20-refreshable-group-input-summary-v1",
        "status": "certified_boundary_confirmed" if args.finalize_heavy_plan else "prepared_awaiting_boundary_confirmation",
        "base": boundary, "groupInput": {"path": str(GROUP_INPUT.relative_to(ROOT))},
        "rows": len(group), "baseOwnedPairs": len(base_pairs), "snapshotOwnedPairs": len(accepted),
        "deltaPairs": len(delta), "deltaLabels": len(delta_labels), "targetLabels": TARGET_LABELS,
        "targetRows": TARGET_ROWS, "targetGeneratedAtMin": min(row[5] for row in targets),
        "targetGeneratedAtMax": max(row[5] for row in targets), "heavyPlanFinalized": bool(args.finalize_heavy_plan),
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    inventory = {
        "schemaVersion": "v20-accepted-scoreable-source-inventory-v1", "status": "certified",
        "base": boundary, "receiptBoundary": {RECEIPT_ID: receipt}, "queuedReceiptExclusion": [],
        "ledgerSnapshot": {"acceptedScoreablePairs": len(accepted), "acceptedScoreableAnchorRows": anchor_rows, "latestSubmissionSyncEpoch": latest_sync},
        "deltaPairCount": len(delta), "deltaLabelCount": len(delta_labels),
        "deltaPairs": [pair_text(pair) for pair in sorted_delta], "acceptedAnchorsByPair": anchors,
        "targetSnapshot": [{"pair": pair_text(pair), "teamCount": target_map[pair][0], "discovered": target_map[pair][1], "minimumDiscAbs": target_map[pair][2], "generatedAt": target_map[pair][3]} for pair in sorted_delta],
        "collisionCensus": {"baselinePairCollisions": len(delta & baseline), "priorFrozenSignatureCollisions": len(delta & prior_signatures), "unanchoredDeltaPairs": len(delta) - len(anchors)},
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    assert_clean = prior.prior.prior.assert_coefficient_free
    for value, name in ((group, "group"), (census, "census"), (summary, "summary"), (inventory, "inventory")):
        assert_clean(value, name)
    helper.atomic_text(GROUP_INPUT, helper.canonical_jsonl(group))
    helper.atomic_text(CENSUS_INPUT, helper.canonical_jsonl(census))
    summary["groupInput"]["sha256"] = helper.sha256_path(GROUP_INPUT)
    helper.atomic_json(GROUP_SUMMARY, summary)
    helper.atomic_json(INVENTORY, inventory)
    argv = ["caffeinate", "-i", "sage", "-python", "agent_index24_missing_pair_census.sage.py", "--input", str(CENSUS_INPUT.relative_to(ROOT))]
    for artifact in prior_maps:
        argv.extend(["--prior-map", artifact["path"]])
    argv.extend(["--signature-aware", "--prior-input", "data/autopilot_pair_delta_20260722_v19/group_input.jsonl", "--output", str(OUTPUT.relative_to(ROOT)), "--shard-index", "0", "--shard-count", "1", "--checkpoint-every", "1"])
    guarded = None
    if args.finalize_heavy_plan:
        guarded = ["/bin/sh", "-c", f"test ! -e {shlex.quote(str(OUTPUT.relative_to(ROOT)))} && exec {shlex.join(argv)}"]
    plan = {
        "schemaVersion": "v20-pair-delta-provenance-plan-v1", "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_one_heavy_worker" if args.finalize_heavy_plan else "prepared_awaiting_boundary_confirmation",
        "artifacts": {"groupInput": helper.relative_artifact(GROUP_INPUT), "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY), "censusInput": helper.relative_artifact(CENSUS_INPUT), "exactSourceInventory": helper.relative_artifact(INVENTORY), "preparer": helper.relative_artifact(Path(__file__).resolve()), "priorMaps": prior_maps, "worker": helper.relative_artifact(ROOT / "agent_index24_missing_pair_census.sage.py"), "base": boundary},
        "delta": {"selectedLabels": len(delta_labels), "selectedSignatures": len(delta), "pairs": [pair_text(pair) for pair in sorted_delta], "signatureBaseline": "completed pinned v19 boundary, 7/7 labels, 8/8 signatures, zero routes"},
        "execution": {"heavyWorkerLaunched": False, "heavyCommandFrozen": bool(args.finalize_heavy_plan), "guardedPlannedCommand": guarded, "outputAbsentAtPreparation": True, "outputAbsentGuardRequired": True, "requiresRootHeavyWorkerClearance": True, "requiresAcceptedBoundaryConfirmation": not args.finalize_heavy_plan, "expectedDeltaPairsAtFinalization": args.expected_delta_pairs},
        "coefficientMaterialIncluded": False, "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    assert_clean(plan, "plan")
    helper.atomic_json(PLAN, plan)
    audit = {
        "schemaVersion": "v20-light-preflight-audit-v1", "sealedAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_safe_for_one_heavy_worker" if args.finalize_heavy_plan else "certified_light_snapshot_awaiting_boundary_confirmation",
        "base": {"version": "v19", "frozenOwnedPairs": len(base_pairs), "completedCensusRows": 7, "conditionalRoutes": 0, "artifacts": boundary},
        "receiptBoundary": {RECEIPT_ID: receipt}, "queuedReceiptExclusion": [],
        "snapshot": {"acceptedScoreablePairs": len(accepted), "deltaPairs": len(delta), "deltaLabels": len(delta_labels), "groupRows": len(group), "targetLabels": TARGET_LABELS, "targetRows": TARGET_ROWS},
        "checks": {"v19BoundaryPinned": True, "v19CompletedCensusExactZeroRoutes": True, "verifiedV17GoldReceipt7f6cd8": True, "noNewQueuedReceiptBoundary": True, "acceptedScoreableDeltaExact": True, "provenanceAnchorsComplete": len(anchors) == len(delta), "targetCacheCompleteAndUnique": True, "signatureIsolationExact": helper.source_pairs(census) == delta, "baselineCollisionCountZero": not bool(delta & baseline), "priorFrozenSignatureCollisionCountZero": not bool(delta & prior_signatures), "priorMapChainCompleteThroughV19": len(prior_maps) == 23, "outputAbsentGuardEnabled": True, "coefficientAndCredentialScanClean": True},
        "artifacts": {"groupInput": helper.relative_artifact(GROUP_INPUT), "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY), "censusInput": helper.relative_artifact(CENSUS_INPUT), "exactSourceInventory": helper.relative_artifact(INVENTORY), "provenancePlan": helper.relative_artifact(PLAN), "preparer": helper.relative_artifact(Path(__file__).resolve())},
        "workerPreflight": {"selectedRows": len(delta_labels), "selectedSignatures": len(delta), "signatureAware": True, "shardCount": 1, "checkpointEvery": 1, "outputAbsentAtSeal": not OUTPUT.exists(), "heavyCommandFrozen": bool(args.finalize_heavy_plan)},
        "coefficientMaterialIncluded": False, "credentialMaterialIncluded": False,
        "sideEffects": {"heavyWorkerLaunched": False, "networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0, "writesLimitedToV20Artifacts": True},
    }
    if not all(audit["checks"].values()):
        raise ValueError("v20 preflight failed")
    assert_clean(audit, "preflight")
    helper.atomic_json(PREFLIGHT, audit)
    print(json.dumps({"status": plan["status"], "baseOwnedPairs": len(base_pairs), "snapshotOwnedPairs": len(accepted), "deltaPairs": len(delta), "selectedWorkerRows": len(delta_labels), "selectedWorkerSignatures": len(delta), "heavyCommandFrozen": bool(args.finalize_heavy_plan), "heavyWorkerLaunched": False, "plan": str(PLAN.relative_to(ROOT)), "preflight": str(PREFLIGHT.relative_to(ROOT))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
