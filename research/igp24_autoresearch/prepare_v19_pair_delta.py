#!/usr/bin/env python3
"""Light-only two-phase post-v18 pair-delta preparer."""

from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import prepare_v11_pair_delta as helper
import prepare_v18_pair_delta as prior


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V18 = ROOT / "data/autopilot_pair_delta_20260722_v18"
V19 = ROOT / "data/autopilot_pair_delta_20260722_v19"
GROUP_INPUT = V19 / "group_input.jsonl"
GROUP_SUMMARY = V19 / "group_input_summary.json"
CENSUS_INPUT = V19 / "census_input.jsonl"
INVENTORY = V19 / "exact_source_inventory.json"
PLAN = V19 / "provenance_plan.json"
PREFLIGHT = V19 / "preflight_audit_summary.json"
OUTPUT = V19 / "missing_pair_all.jsonl"

BASE_ROWS = 13_347
BASE_PAIRS = 22_020
BASE_DELTA_LABELS = 17
BASE_DELTA_PAIRS = 17
TARGET_ROWS = 165_836
TARGET_LABELS = 25_000

PINS = {
    "prepare_v18_pair_delta.py": "f2b403ec46fd1393e829be099bc6a4597ab6d7ed70bda745c18c007c81a7eb6c",
    "data/autopilot_pair_delta_20260722_v18/group_input.jsonl": "f8ab85def042213d3baba75f6e371d606e741a1e1e89cf7bc0a91bd2902b6f43",
    "data/autopilot_pair_delta_20260722_v18/group_input_summary.json": "9bff10b00cfe7bc7c46d3bb301d86f3fc92313a77e7b6deeacef1820306e611c",
    "data/autopilot_pair_delta_20260722_v18/census_input.jsonl": "cf9cf92b4793677886c11595e334070fd3787cf3082b6f53eec0212dd12046c0",
    "data/autopilot_pair_delta_20260722_v18/missing_pair_all.jsonl": "d36455e9d344d629c64c1daec8ed3169abc0f9d5126450ec8cdc2bd8a25a8ea5",
    "data/autopilot_pair_delta_20260722_v18/exact_source_inventory.json": "1046eb481b5fc386690dc65aac704cda1da9267db15d3a509228195dacdf1542",
    "data/autopilot_pair_delta_20260722_v18/provenance_plan.json": "f6546ae0d2a5bdc1fe58adaa3233052b3dad5a41699ec2e03256fb8c2f2d92ca",
    "data/autopilot_pair_delta_20260722_v18/preflight_audit_summary.json": "8b4a60768ef00a0fa6437343983469a17b650e04aa00c8558628a147f38d84c6",
    "receipts/sub_ebc16254512a411b902ac0212d62f21b.json": "08be50931063c37662af52bcb8839c441e98c5f9e680fae2efedc3a6ffdbc24d",
    "receipts/sub_7f6cd8ec78604eaca6fa5a8e373f90e5.json": "d3fe797160de17e68a3c7ca985b98d2b46db944dc73357f511024774bb5c1c8f",
}
VERIFIED_RECEIPT = {"sub_ebc16254512a411b902ac0212d62f21b": 8}
QUEUED_FOR_V20 = {"sub_7f6cd8ec78604eaca6fa5a8e373f90e5": 2}


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalize-heavy-plan", action="store_true")
    parser.add_argument("--expected-delta-pairs", type=int)
    return parser.parse_args()


def pin(relative: str) -> None:
    path = ROOT / relative
    if not path.is_file() or helper.sha256_path(path) != PINS[relative]:
        raise ValueError(f"pinned v18 artifact changed: {relative}")


def validate_base() -> tuple[set[tuple[str, int]], list[dict], set[tuple[str, int]]]:
    for relative in PINS:
        pin(relative)
    group = helper.read_jsonl(V18 / "group_input.jsonl")
    census = helper.read_jsonl(V18 / "census_input.jsonl")
    completed = helper.read_jsonl(V18 / "missing_pair_all.jsonl")
    summary = helper.read_json(V18 / "group_input_summary.json")
    inventory = helper.read_json(V18 / "exact_source_inventory.json")
    plan = helper.read_json(V18 / "provenance_plan.json")
    preflight = helper.read_json(V18 / "preflight_audit_summary.json")
    owned = helper.source_pairs(group)
    selected = helper.source_pairs(census)
    done = {
        (str(row.get("sourceLabel")), int(r))
        for row in completed for r in row.get("sourceR") or []
    }
    inventory_pairs = {
        prior.prior.parsed_pair(value)
        for value in inventory.get("deltaPairs") or []
    }
    if (
        len(group) != BASE_ROWS or len(owned) != BASE_PAIRS
        or len(census) != BASE_ROWS or len(completed) != BASE_DELTA_LABELS
        or len(selected) != BASE_DELTA_PAIRS or selected != done
        or selected != inventory_pairs
        or sum(len(row.get("routes") or []) for row in completed) != 0
        or any(row.get("status") != "certified" or not helper.certificate_is_exact(row) for row in completed)
        or summary.get("status") != "certified_boundary_confirmed"
        or summary.get("heavyPlanFinalized") is not True
        or int(summary.get("snapshotOwnedPairs", -1)) != BASE_PAIRS
        or int(summary.get("deltaPairs", -1)) != BASE_DELTA_PAIRS
        or int(summary.get("deltaLabels", -1)) != BASE_DELTA_LABELS
        or inventory.get("status") != "certified"
        or int(inventory.get("deltaPairCount", -1)) != BASE_DELTA_PAIRS
        or set((inventory.get("acceptedAnchorsByPair") or {}))
        != {prior.prior.pair_text(pair) for pair in selected}
        or plan.get("status") != "ready_for_one_heavy_worker"
        or (plan.get("execution") or {}).get("heavyCommandFrozen") is not True
        or preflight.get("status") != "certified_safe_for_one_heavy_worker"
        or not all((preflight.get("checks") or {}).values())
    ):
        raise ValueError("v18 frozen zero-route boundary changed")
    maps = list((plan.get("artifacts") or {}).get("priorMaps") or [])
    if len(maps) != 21:
        raise ValueError("v18 prior chain count changed")
    signatures = set()
    for artifact in maps:
        path = ROOT / artifact["path"]
        if not path.is_file() or helper.sha256_path(path) != artifact["sha256"]:
            raise ValueError(f"prior map changed: {path}")
        for row in helper.read_jsonl(path):
            if row.get("sourceLabel") is not None:
                signatures.update((str(row["sourceLabel"]), int(r)) for r in row.get("sourceR") or [])
    maps.append(helper.relative_artifact(V18 / "missing_pair_all.jsonl"))
    signatures.update(done)
    if not signatures <= owned:
        raise ValueError("prior chain through v18 escapes ownership")
    return owned, maps, signatures


def validate_receipts(connection: sqlite3.Connection) -> dict:
    result = {}
    for submission_id, expected in VERIFIED_RECEIPT.items():
        receipt = helper.read_json(ROOT / f"receipts/{submission_id}.json")
        hashes = prior.manifest_hashes(receipt, expected)
        submission = connection.execute(
            "SELECT queued_count,verified_count,failed_count,synced_at FROM submissions WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
        rows = list(connection.execute(
            "SELECT p.coefficient_hash,v.label,v.r,v.status,v.scoreable,v.scoring_status,v.in_baseline "
            "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
            "WHERE p.submission_id=? ORDER BY p.polynomial_index", (submission_id,),
        ))
        if (
            submission is None or tuple(map(int, submission[:3])) != (0, expected, 0)
            or len(rows) != expected or {str(row[0]) for row in rows} != hashes
            or len({(str(row[1]), int(row[2])) for row in rows}) != expected
            or any(str(row[3]) != "accepted" or int(row[4]) != 1 or str(row[5]) != "scoreable" or int(row[6]) != 0 for row in rows)
        ):
            raise ValueError("twist receipt is not exactly verified")
        result[submission_id] = {
            "receipt": helper.relative_artifact(ROOT / f"receipts/{submission_id}.json"),
            "manifestSha256": receipt["manifestHash"], "verifiedAcceptedScoreable": expected,
            "distinctPairs": expected, "syncedAtEpoch": float(submission[3]),
        }
    queued_id, expected = next(iter(QUEUED_FOR_V20.items()))
    queued = helper.read_json(ROOT / f"receipts/{queued_id}.json")
    prior.manifest_hashes(queued, expected)
    response = queued.get("response") or {}
    ledger_count = int(connection.execute(
        "SELECT COUNT(*) FROM verifications WHERE submission_id=?", (queued_id,),
    ).fetchone()[0])
    submission = connection.execute(
        "SELECT verified_count,failed_count FROM submissions WHERE submission_id=?", (queued_id,),
    ).fetchone()
    if (
        int(queued.get("polynomials", -1)) != expected
        or int(response.get("rejectedCount", -1)) != 0 or response.get("failedPolynomials")
        or ledger_count or (submission is not None and (int(submission[0]) or int(submission[1])))
    ):
        raise ValueError("queued v17 gold receipt entered v19 boundary")
    return result


def main() -> int:
    parsed = args()
    if parsed.finalize_heavy_plan and parsed.expected_delta_pairs is None:
        raise ValueError("--expected-delta-pairs required")
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite heavy output: {OUTPUT}")
    base_pairs, prior_maps, prior_signatures = validate_base()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        receipts = validate_receipts(connection)
        baseline = {(str(label), int(r)) for label, r in connection.execute("SELECT label,r FROM baseline_pairs")}
        accepted = {(str(label), int(r)) for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE status='accepted' AND scoreable=1"
        )}
        if not base_pairs <= accepted:
            raise ValueError("ledger regressed behind v18")
        delta = accepted - base_pairs
        if not delta or delta & baseline or delta & prior_signatures:
            raise ValueError("post-v18 delta empty or colliding")
        if parsed.expected_delta_pairs is not None and len(delta) != parsed.expected_delta_pairs:
            raise ValueError(f"expected {parsed.expected_delta_pairs} pairs, found {len(delta)}")
        anchors = prior.prior.accepted_anchors(connection, delta)
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
    labels = sorted(set(owned_by_label) | set(gold_by_label), key=lambda x: int(x[3:]))
    group = [{
        "goldR": sorted(gold_by_label.get(label, set())), "isGoldTarget": label in gold_by_label,
        "isOwnedSource": label in owned_by_label, "label": label,
        "sourceR": sorted(owned_by_label.get(label, set())), "t": int(label[3:]),
    } for label in labels]
    if helper.source_pairs(group) != accepted:
        raise ValueError("group ownership mismatch")
    census = []
    for row in group:
        selected = sorted(r for label, r in delta if label == row["label"])
        census.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if helper.source_pairs(census) != delta:
        raise ValueError("delta isolation mismatch")
    sorted_delta = sorted(delta, key=prior.prior.pair_key)
    delta_labels = {label for label, _ in delta}
    boundary = {
        "groupInput": helper.relative_artifact(V18 / "group_input.jsonl"),
        "censusInput": helper.relative_artifact(V18 / "census_input.jsonl"),
        "completedCensus": helper.relative_artifact(V18 / "missing_pair_all.jsonl"),
        "preflight": helper.relative_artifact(V18 / "preflight_audit_summary.json"),
    }
    summary = {
        "schemaVersion": "v19-refreshable-group-input-summary-v1",
        "status": "certified_boundary_confirmed" if parsed.finalize_heavy_plan else "prepared_awaiting_boundary_confirmation",
        "base": boundary, "groupInput": {"path": str(GROUP_INPUT.relative_to(ROOT))},
        "rows": len(group), "baseOwnedPairs": len(base_pairs), "snapshotOwnedPairs": len(accepted),
        "deltaPairs": len(delta), "deltaLabels": len(delta_labels), "targetLabels": TARGET_LABELS,
        "targetRows": TARGET_ROWS, "targetGeneratedAtMin": min(row[5] for row in targets),
        "targetGeneratedAtMax": max(row[5] for row in targets), "heavyPlanFinalized": bool(parsed.finalize_heavy_plan),
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    inventory = {
        "schemaVersion": "v19-accepted-scoreable-source-inventory-v1", "status": "certified",
        "base": boundary, "receiptBoundary": receipts, "queuedForV20Excluded": QUEUED_FOR_V20,
        "ledgerSnapshot": {"acceptedScoreablePairs": len(accepted), "acceptedScoreableAnchorRows": anchor_rows, "latestSubmissionSyncEpoch": latest_sync},
        "deltaPairCount": len(delta), "deltaLabelCount": len(delta_labels),
        "deltaPairs": [prior.prior.pair_text(pair) for pair in sorted_delta],
        "acceptedAnchorsByPair": anchors,
        "targetSnapshot": [{"pair": prior.prior.pair_text(pair), "teamCount": target_map[pair][0], "discovered": target_map[pair][1], "minimumDiscAbs": target_map[pair][2], "generatedAt": target_map[pair][3]} for pair in sorted_delta],
        "collisionCensus": {"baselinePairCollisions": len(delta & baseline), "priorFrozenSignatureCollisions": len(delta & prior_signatures), "unanchoredDeltaPairs": len(delta) - len(anchors)},
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    for value, name in ((group, "group"), (census, "census"), (summary, "summary"), (inventory, "inventory")):
        prior.prior.assert_coefficient_free(value, name)
    helper.atomic_text(GROUP_INPUT, helper.canonical_jsonl(group))
    helper.atomic_text(CENSUS_INPUT, helper.canonical_jsonl(census))
    summary["groupInput"]["sha256"] = helper.sha256_path(GROUP_INPUT)
    helper.atomic_json(GROUP_SUMMARY, summary)
    helper.atomic_json(INVENTORY, inventory)
    argv = ["caffeinate", "-i", "sage", "-python", "agent_index24_missing_pair_census.sage.py", "--input", str(CENSUS_INPUT.relative_to(ROOT))]
    for artifact in prior_maps:
        argv.extend(["--prior-map", artifact["path"]])
    argv.extend(["--signature-aware", "--prior-input", "data/autopilot_pair_delta_20260722_v18/group_input.jsonl", "--output", str(OUTPUT.relative_to(ROOT)), "--shard-index", "0", "--shard-count", "1", "--checkpoint-every", "1"])
    guarded = None
    if parsed.finalize_heavy_plan:
        guarded = ["/bin/sh", "-c", f"test ! -e {shlex.quote(str(OUTPUT.relative_to(ROOT)))} && exec {shlex.join(argv)}"]
    plan = {
        "schemaVersion": "v19-pair-delta-provenance-plan-v1", "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_one_heavy_worker" if parsed.finalize_heavy_plan else "prepared_awaiting_boundary_confirmation",
        "artifacts": {"groupInput": helper.relative_artifact(GROUP_INPUT), "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY), "censusInput": helper.relative_artifact(CENSUS_INPUT), "exactSourceInventory": helper.relative_artifact(INVENTORY), "preparer": helper.relative_artifact(Path(__file__).resolve()), "priorMaps": prior_maps, "worker": helper.relative_artifact(ROOT / "agent_index24_missing_pair_census.sage.py"), "base": boundary},
        "delta": {"selectedLabels": len(delta_labels), "selectedSignatures": len(delta), "pairs": [prior.prior.pair_text(pair) for pair in sorted_delta], "signatureBaseline": "completed pinned v18 boundary, 17/17 signatures, zero routes"},
        "execution": {"heavyWorkerLaunched": False, "heavyCommandFrozen": bool(parsed.finalize_heavy_plan), "guardedPlannedCommand": guarded, "outputAbsentAtPreparation": True, "outputAbsentGuardRequired": True, "requiresRootHeavyWorkerClearance": True, "requiresAcceptedBoundaryConfirmation": not parsed.finalize_heavy_plan, "expectedDeltaPairsAtFinalization": parsed.expected_delta_pairs},
        "coefficientMaterialIncluded": False, "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    prior.prior.assert_coefficient_free(plan, "plan")
    helper.atomic_json(PLAN, plan)
    audit = {
        "schemaVersion": "v19-light-preflight-audit-v1", "sealedAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_safe_for_one_heavy_worker" if parsed.finalize_heavy_plan else "certified_light_snapshot_awaiting_boundary_confirmation",
        "base": {"version": "v18", "frozenOwnedPairs": len(base_pairs), "completedCensusRows": 17, "conditionalRoutes": 0, "artifacts": boundary},
        "receiptBoundary": receipts, "queuedForV20Excluded": QUEUED_FOR_V20,
        "snapshot": {"acceptedScoreablePairs": len(accepted), "deltaPairs": len(delta), "deltaLabels": len(delta_labels), "groupRows": len(group), "targetLabels": TARGET_LABELS, "targetRows": TARGET_ROWS},
        "checks": {"v18BoundaryPinned": True, "v18CompletedCensusExactZeroRoutes": True, "verifiedTwistReceiptEbc162": len(receipts) == 1, "queuedV17Gold7f6cd8ExcludedForV20": True, "acceptedScoreableDeltaExact": True, "provenanceAnchorsComplete": len(anchors) == len(delta), "targetCacheCompleteAndUnique": True, "signatureIsolationExact": helper.source_pairs(census) == delta, "baselineCollisionCountZero": not bool(delta & baseline), "priorFrozenSignatureCollisionCountZero": not bool(delta & prior_signatures), "priorMapChainCompleteThroughV18": len(prior_maps) == 22, "outputAbsentGuardEnabled": True, "coefficientAndCredentialScanClean": True},
        "artifacts": {"groupInput": helper.relative_artifact(GROUP_INPUT), "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY), "censusInput": helper.relative_artifact(CENSUS_INPUT), "exactSourceInventory": helper.relative_artifact(INVENTORY), "provenancePlan": helper.relative_artifact(PLAN), "preparer": helper.relative_artifact(Path(__file__).resolve())},
        "workerPreflight": {"selectedRows": len(delta_labels), "selectedSignatures": len(delta), "signatureAware": True, "shardCount": 1, "checkpointEvery": 1, "outputAbsentAtSeal": not OUTPUT.exists(), "heavyCommandFrozen": bool(parsed.finalize_heavy_plan)},
        "coefficientMaterialIncluded": False, "credentialMaterialIncluded": False,
        "sideEffects": {"heavyWorkerLaunched": False, "networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0, "writesLimitedToV19Artifacts": True},
    }
    if not all(audit["checks"].values()):
        raise ValueError("v19 preflight failed")
    prior.prior.assert_coefficient_free(audit, "preflight")
    helper.atomic_json(PREFLIGHT, audit)
    print(json.dumps({"status": plan["status"], "baseOwnedPairs": len(base_pairs), "snapshotOwnedPairs": len(accepted), "deltaPairs": len(delta), "selectedWorkerRows": len(delta_labels), "selectedWorkerSignatures": len(delta), "heavyCommandFrozen": bool(parsed.finalize_heavy_plan), "heavyWorkerLaunched": False, "plan": str(PLAN.relative_to(ROOT)), "preflight": str(PREFLIGHT.relative_to(ROOT))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
