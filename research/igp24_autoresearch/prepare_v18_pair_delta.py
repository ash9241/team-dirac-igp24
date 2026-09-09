#!/usr/bin/env python3
"""Prepare/finalize the light-only accepted-scoreable post-v17 pair delta.

The v17 ownership snapshot and completed 39-row census are hash-pinned.  The
default phase refreshes the exact delta; finalization freezes one guarded
single-worker census command after an exact cardinality check.  No phase runs
Sage/GAP, uses the network, submits, or writes the ledger.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import prepare_v11_pair_delta as helper
import prepare_v17_pair_delta as prior
import stage_single_exact_census as exact_census


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V17 = ROOT / "data/autopilot_pair_delta_20260722_v17"
V18 = ROOT / "data/autopilot_pair_delta_20260722_v18"
GROUP_INPUT = V18 / "group_input.jsonl"
GROUP_SUMMARY = V18 / "group_input_summary.json"
CENSUS_INPUT = V18 / "census_input.jsonl"
INVENTORY = V18 / "exact_source_inventory.json"
PLAN = V18 / "provenance_plan.json"
PREFLIGHT = V18 / "preflight_audit_summary.json"
OUTPUT = V18 / "missing_pair_all.jsonl"

EXPECTED_V17_GROUP_ROWS = 13_338
EXPECTED_V17_OWNED_PAIRS = 22_003
EXPECTED_V17_DELTA_LABELS = 39
EXPECTED_V17_DELTA_PAIRS = 47
EXPECTED_V17_CENSUS_ROUTES = 4
EXPECTED_TARGET_LABELS = 25_000
EXPECTED_TARGET_ROWS = 165_836

V17_PINS = {
    "prepare_v17_pair_delta.py":
        "0fc588eb34c6432bdcd39a4e39c9c7aa3f20fbbc07f6ab2729f82fd7209cc24c",
    "data/autopilot_pair_delta_20260722_v17/group_input.jsonl":
        "0f3918dd0800bc9fa7ca8e95bf5ad3c54c5126ab0171ee9d199f3fdc074bd18f",
    "data/autopilot_pair_delta_20260722_v17/group_input_summary.json":
        "7ce2070579fd89c6fe60a137045707fcc3687ea26d9e3fed48a7423b3e8b9d85",
    "data/autopilot_pair_delta_20260722_v17/census_input.jsonl":
        "451b9a2f775b649f9ec8b8af69da9676419afa0486b09cec7a1fdf0ae7646002",
    "data/autopilot_pair_delta_20260722_v17/missing_pair_all.jsonl":
        "b2cb34506ae0329245ab2a304c7a3a72ed62ef0627c5381227fe4900671320c8",
    "data/autopilot_pair_delta_20260722_v17/exact_source_inventory.json":
        "2d6e989e446d861956addb7f38a3a3cc47c0366d973aa01f8d7d97a3b7c2ab75",
    "data/autopilot_pair_delta_20260722_v17/provenance_plan.json":
        "cd771b6d41df8ebfd0220d8cee702916b029a5f68a8ff29006ce1bb247346907",
    "data/autopilot_pair_delta_20260722_v17/preflight_audit_summary.json":
        "afb804af9c7a1b0e046b075ffe73b9a8cf2cf40945621679b2eb0d3ac04fc26d",
    "receipts/sub_6eeaa15d8d994fd19646243de90b4b09.json":
        "9a3b161e09b2b472bfced51df1c531f46b8ce3093d97345ac5c2d19c52c7d4eb",
    "receipts/sub_bc7a3dd9294543bd813bed8ba413972b.json":
        "6b8ea80c27ba31a56be22684e58b145cbd1e982aaddbf2a9842cde02d682ad29",
    "receipts/sub_ebc16254512a411b902ac0212d62f21b.json":
        "08be50931063c37662af52bcb8839c441e98c5f9e680fae2efedc3a6ffdbc24d",
}

RECEIPT_BOUNDARY = {
    "sub_6eeaa15d8d994fd19646243de90b4b09": 3,
    "sub_bc7a3dd9294543bd813bed8ba413972b": 14,
}
QUEUED_FOR_V19 = {
    "sub_ebc16254512a411b902ac0212d62f21b": 8,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalize-heavy-plan", action="store_true")
    parser.add_argument("--expected-delta-pairs", type=int)
    return parser.parse_args()


def require_pin(relative: str) -> None:
    path = ROOT / relative
    if not path.is_file() or helper.sha256_path(path) != V17_PINS[relative]:
        raise ValueError(f"pinned v17 artifact changed: {relative}")


def validate_v17_boundary() -> tuple[
    set[tuple[str, int]], list[dict], set[tuple[str, int]]
]:
    for relative in V17_PINS:
        require_pin(relative)
    group_rows = helper.read_jsonl(V17 / "group_input.jsonl")
    census_rows = helper.read_jsonl(V17 / "census_input.jsonl")
    completed_rows = helper.read_jsonl(V17 / "missing_pair_all.jsonl")
    summary = helper.read_json(V17 / "group_input_summary.json")
    inventory = helper.read_json(V17 / "exact_source_inventory.json")
    plan = helper.read_json(V17 / "provenance_plan.json")
    preflight = helper.read_json(V17 / "preflight_audit_summary.json")
    owned_pairs = helper.source_pairs(group_rows)
    selected_pairs = helper.source_pairs(census_rows)
    completed_pairs = {
        (str(row.get("sourceLabel")), int(r))
        for row in completed_rows
        for r in row.get("sourceR") or []
    }
    inventory_pairs = {
        prior.parsed_pair(value) for value in inventory.get("deltaPairs") or []
    }
    route_count = sum(len(row.get("routes") or []) for row in completed_rows)
    if (
        len(group_rows) != EXPECTED_V17_GROUP_ROWS
        or len(owned_pairs) != EXPECTED_V17_OWNED_PAIRS
        or len(census_rows) != EXPECTED_V17_GROUP_ROWS
        or len(completed_rows) != EXPECTED_V17_DELTA_LABELS
        or len(selected_pairs) != EXPECTED_V17_DELTA_PAIRS
        or selected_pairs != completed_pairs
        or selected_pairs != inventory_pairs
        or route_count != EXPECTED_V17_CENSUS_ROUTES
        or any(
            row.get("status") != "certified"
            or not helper.certificate_is_exact(row)
            for row in completed_rows
        )
    ):
        raise ValueError("v17 completed census boundary is not exact")
    if (
        summary.get("status") != "certified_boundary_confirmed"
        or summary.get("heavyPlanFinalized") is not True
        or int(summary.get("baseOwnedPairs", -1)) != 21_956
        or int(summary.get("snapshotOwnedPairs", -1))
        != EXPECTED_V17_OWNED_PAIRS
        or int(summary.get("deltaPairs", -1)) != EXPECTED_V17_DELTA_PAIRS
        or int(summary.get("deltaLabels", -1)) != EXPECTED_V17_DELTA_LABELS
        or inventory.get("status") != "certified"
        or int(inventory.get("deltaPairCount", -1))
        != EXPECTED_V17_DELTA_PAIRS
        or set((inventory.get("acceptedAnchorsByPair") or {}))
        != {prior.pair_text(pair) for pair in selected_pairs}
        or plan.get("status") != "ready_for_one_heavy_worker"
        or (plan.get("execution") or {}).get("heavyCommandFrozen") is not True
        or preflight.get("status") != "certified_safe_for_one_heavy_worker"
        or not all((preflight.get("checks") or {}).values())
    ):
        raise ValueError("v17 frozen plan envelope changed")
    prior_artifacts = list((plan.get("artifacts") or {}).get("priorMaps") or [])
    if len(prior_artifacts) != 20:
        raise ValueError("v17 prior chain cardinality changed")
    prior_signature_pairs = set()
    for artifact in prior_artifacts:
        path = ROOT / str(artifact["path"])
        if not path.is_file() or helper.sha256_path(path) != str(artifact["sha256"]):
            raise ValueError(f"v17 prior map changed: {path}")
        for row in helper.read_jsonl(path):
            prior_signature_pairs.update(
                (str(row.get("sourceLabel")), int(r))
                for r in row.get("sourceR") or []
                if row.get("sourceLabel") is not None
            )
    prior_artifacts.append(helper.relative_artifact(V17 / "missing_pair_all.jsonl"))
    prior_signature_pairs.update(completed_pairs)
    if not prior_signature_pairs <= owned_pairs:
        raise ValueError("prior chain through v17 escapes frozen ownership")
    return owned_pairs, prior_artifacts, prior_signature_pairs


def manifest_hashes(receipt: dict, expected: int) -> set[str]:
    path = Path(str(receipt["manifest"])).resolve()
    if not path.is_file() or exact_census.sha256_path(path) != str(receipt["manifestHash"]):
        raise ValueError("receipt manifest changed")
    hashes = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = exact_census.canonical_polynomial_line(raw.strip())
        if line is not None:
            hashes.add(exact_census.sha256_bytes(line.encode("ascii")))
    if len(hashes) != expected:
        raise ValueError("receipt manifest count changed")
    return hashes


def validate_receipts(connection: sqlite3.Connection) -> dict:
    result = {}
    for submission_id, expected in RECEIPT_BOUNDARY.items():
        receipt = helper.read_json(ROOT / f"receipts/{submission_id}.json")
        hashes = manifest_hashes(receipt, expected)
        submission = connection.execute(
            "SELECT queued_count,verified_count,failed_count,synced_at "
            "FROM submissions WHERE submission_id=?", (submission_id,),
        ).fetchone()
        rows = list(connection.execute(
            "SELECT p.coefficient_hash,v.label,v.r,v.status,v.scoreable,"
            "v.scoring_status,v.in_baseline FROM polynomials p JOIN "
            "verifications v USING(submission_id,polynomial_index) "
            "WHERE p.submission_id=? ORDER BY p.polynomial_index",
            (submission_id,),
        ))
        if (
            submission is None
            or tuple(map(int, submission[:3])) != (0, expected, 0)
            or len(rows) != expected
            or {str(row[0]) for row in rows} != hashes
            or len({(str(row[1]), int(row[2])) for row in rows}) != expected
            or any(
                str(row[3]) != "accepted" or int(row[4]) != 1
                or str(row[5]) != "scoreable" or int(row[6]) != 0
                for row in rows
            )
        ):
            raise ValueError(f"receipt not exactly verified: {submission_id}")
        result[submission_id] = {
            "receipt": helper.relative_artifact(ROOT / f"receipts/{submission_id}.json"),
            "manifestSha256": str(receipt["manifestHash"]),
            "verifiedAcceptedScoreable": expected,
            "distinctPairs": expected,
            "syncedAtEpoch": float(submission[3]),
        }
    queued_id, expected = next(iter(QUEUED_FOR_V19.items()))
    receipt = helper.read_json(ROOT / f"receipts/{queued_id}.json")
    response = receipt.get("response") or {}
    manifest_hashes(receipt, expected)
    submission = connection.execute(
        "SELECT verified_count,failed_count FROM submissions WHERE submission_id=?",
        (queued_id,),
    ).fetchone()
    verification_count = int(connection.execute(
        "SELECT COUNT(*) FROM verifications WHERE submission_id=?", (queued_id,),
    ).fetchone()[0])
    if (
        int(receipt.get("polynomials", -1)) != expected
        or int(response.get("rejectedCount", -1)) != 0
        or response.get("failedPolynomials")
        or verification_count
        or (submission is not None and (int(submission[0]) or int(submission[1])))
    ):
        raise ValueError("queued twist receipt entered v18 boundary")
    return result


def main() -> int:
    args = parse_args()
    if args.finalize_heavy_plan and args.expected_delta_pairs is None:
        raise ValueError("--expected-delta-pairs required")
    if args.expected_delta_pairs is not None and args.expected_delta_pairs <= 0:
        raise ValueError("expected delta must be positive")
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite heavy output: {OUTPUT}")
    v17_pairs, prior_artifacts, prior_signature_pairs = validate_v17_boundary()

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        receipt_boundary = validate_receipts(connection)
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        accepted_pairs = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE status='accepted' AND scoreable=1"
            )
        }
        if not v17_pairs <= accepted_pairs:
            raise ValueError("ledger regressed behind v17 boundary")
        delta = accepted_pairs - v17_pairs
        if not delta or delta & baseline or delta & prior_signature_pairs:
            raise ValueError("post-v17 delta empty or colliding")
        if args.expected_delta_pairs is not None and len(delta) != args.expected_delta_pairs:
            raise ValueError(
                f"expected {args.expected_delta_pairs} delta pairs, found {len(delta)}"
            )
        anchors = prior.accepted_anchors(connection, delta)
        target_records = [
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

    target_map = {(row[0], row[1]): row[2:] for row in target_records}
    target_labels = {row[0] for row in target_records}
    if (
        len(target_map) != EXPECTED_TARGET_ROWS
        or len(target_labels) != EXPECTED_TARGET_LABELS
        or not delta <= set(target_map)
    ):
        raise ValueError("current target cache incomplete")
    owned_by_label = {}
    for label, r in accepted_pairs:
        owned_by_label.setdefault(label, set()).add(r)
    gold_by_label = {}
    for pair, state in target_map.items():
        if state[0] == 0 and pair not in baseline and pair not in accepted_pairs:
            gold_by_label.setdefault(pair[0], set()).add(pair[1])
    labels = sorted(set(owned_by_label) | set(gold_by_label), key=lambda x: int(x[3:]))
    group_rows = [{
        "goldR": sorted(gold_by_label.get(label, set())),
        "isGoldTarget": label in gold_by_label,
        "isOwnedSource": label in owned_by_label,
        "label": label,
        "sourceR": sorted(owned_by_label.get(label, set())),
        "t": int(label[3:]),
    } for label in labels]
    if helper.source_pairs(group_rows) != accepted_pairs:
        raise ValueError("v18 group input ownership mismatch")
    census_rows = []
    for row in group_rows:
        selected = sorted(r for label, r in delta if label == row["label"])
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if helper.source_pairs(census_rows) != delta:
        raise ValueError("v18 census isolation mismatch")

    sorted_delta = sorted(delta, key=prior.pair_key)
    delta_labels = {label for label, _ in delta}
    boundary = {
        "groupInput": helper.relative_artifact(V17 / "group_input.jsonl"),
        "censusInput": helper.relative_artifact(V17 / "census_input.jsonl"),
        "completedCensus": helper.relative_artifact(V17 / "missing_pair_all.jsonl"),
        "preflight": helper.relative_artifact(V17 / "preflight_audit_summary.json"),
    }
    group_summary = {
        "schemaVersion": "v18-refreshable-group-input-summary-v1",
        "status": "certified_boundary_confirmed" if args.finalize_heavy_plan else "prepared_awaiting_boundary_confirmation",
        "base": boundary,
        "groupInput": {"path": str(GROUP_INPUT.relative_to(ROOT))},
        "rows": len(group_rows),
        "baseOwnedPairs": len(v17_pairs),
        "snapshotOwnedPairs": len(accepted_pairs),
        "deltaPairs": len(delta),
        "deltaLabels": len(delta_labels),
        "targetLabels": len(target_labels),
        "targetRows": len(target_map),
        "targetGeneratedAtMin": min(row[5] for row in target_records),
        "targetGeneratedAtMax": max(row[5] for row in target_records),
        "heavyPlanFinalized": bool(args.finalize_heavy_plan),
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    inventory = {
        "schemaVersion": "v18-accepted-scoreable-source-inventory-v1",
        "status": "certified",
        "base": boundary,
        "receiptBoundary": receipt_boundary,
        "queuedForV19Excluded": dict(QUEUED_FOR_V19),
        "ledgerSnapshot": {
            "acceptedScoreablePairs": len(accepted_pairs),
            "acceptedScoreableAnchorRows": anchor_rows,
            "latestSubmissionSyncEpoch": latest_sync,
        },
        "deltaPairCount": len(delta),
        "deltaLabelCount": len(delta_labels),
        "deltaPairs": [prior.pair_text(pair) for pair in sorted_delta],
        "acceptedAnchorsByPair": anchors,
        "targetSnapshot": [{
            "pair": prior.pair_text(pair),
            "teamCount": target_map[pair][0],
            "discovered": target_map[pair][1],
            "minimumDiscAbs": target_map[pair][2],
            "generatedAt": target_map[pair][3],
        } for pair in sorted_delta],
        "collisionCensus": {
            "baselinePairCollisions": len(delta & baseline),
            "priorFrozenSignatureCollisions": len(delta & prior_signature_pairs),
            "unanchoredDeltaPairs": len(delta) - len(anchors),
        },
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    for value, name in ((group_rows, "group"), (census_rows, "census"), (group_summary, "summary"), (inventory, "inventory")):
        prior.assert_coefficient_free(value, name)
    helper.atomic_text(GROUP_INPUT, helper.canonical_jsonl(group_rows))
    helper.atomic_text(CENSUS_INPUT, helper.canonical_jsonl(census_rows))
    group_summary["groupInput"]["sha256"] = helper.sha256_path(GROUP_INPUT)
    helper.atomic_json(GROUP_SUMMARY, group_summary)
    helper.atomic_json(INVENTORY, inventory)

    argv = ["caffeinate", "-i", "sage", "-python", "agent_index24_missing_pair_census.sage.py", "--input", str(CENSUS_INPUT.relative_to(ROOT))]
    for artifact in prior_artifacts:
        argv.extend(["--prior-map", str(artifact["path"])])
    argv.extend([
        "--signature-aware", "--prior-input", "data/autopilot_pair_delta_20260722_v17/group_input.jsonl",
        "--output", str(OUTPUT.relative_to(ROOT)), "--shard-index", "0", "--shard-count", "1", "--checkpoint-every", "1",
    ])
    guarded = None
    if args.finalize_heavy_plan:
        guarded = ["/bin/sh", "-c", f"test ! -e {shlex.quote(str(OUTPUT.relative_to(ROOT)))} && exec {shlex.join(argv)}"]
    plan = {
        "schemaVersion": "v18-pair-delta-provenance-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_one_heavy_worker" if args.finalize_heavy_plan else "prepared_awaiting_boundary_confirmation",
        "artifacts": {
            "groupInput": helper.relative_artifact(GROUP_INPUT),
            "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY),
            "censusInput": helper.relative_artifact(CENSUS_INPUT),
            "exactSourceInventory": helper.relative_artifact(INVENTORY),
            "preparer": helper.relative_artifact(Path(__file__).resolve()),
            "priorMaps": prior_artifacts,
            "worker": helper.relative_artifact(ROOT / "agent_index24_missing_pair_census.sage.py"),
            "base": boundary,
        },
        "delta": {
            "selectedLabels": len(delta_labels), "selectedSignatures": len(delta),
            "pairs": [prior.pair_text(pair) for pair in sorted_delta],
            "signatureBaseline": "completed pinned v17 boundary, 39/39 rows, 47/47 signatures, four conditional routes",
        },
        "execution": {
            "heavyWorkerLaunched": False, "heavyCommandFrozen": bool(args.finalize_heavy_plan),
            "guardedPlannedCommand": guarded, "outputAbsentAtPreparation": True,
            "outputAbsentGuardRequired": True, "requiresRootHeavyWorkerClearance": True,
            "requiresAcceptedBoundaryConfirmation": not args.finalize_heavy_plan,
            "expectedDeltaPairsAtFinalization": args.expected_delta_pairs,
        },
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    prior.assert_coefficient_free(plan, "plan")
    helper.atomic_json(PLAN, plan)
    audit = {
        "schemaVersion": "v18-light-preflight-audit-v1",
        "sealedAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_safe_for_one_heavy_worker" if args.finalize_heavy_plan else "certified_light_snapshot_awaiting_boundary_confirmation",
        "base": {"version": "v17", "frozenOwnedPairs": len(v17_pairs), "completedCensusRows": 39, "conditionalRoutes": 4, "artifacts": boundary},
        "receiptBoundary": receipt_boundary,
        "queuedForV19Excluded": dict(QUEUED_FOR_V19),
        "snapshot": {"acceptedScoreablePairs": len(accepted_pairs), "deltaPairs": len(delta), "deltaLabels": len(delta_labels), "groupRows": len(group_rows), "targetLabels": len(target_labels), "targetRows": len(target_map)},
        "checks": {
            "v17BoundaryPinned": True, "v17CompletedCensusExact": True,
            "verifiedReceiptBoundary6eeAndBc7": len(receipt_boundary) == 2,
            "queuedTwistEbc162ExcludedForV19": True, "acceptedScoreableDeltaExact": True,
            "provenanceAnchorsComplete": len(anchors) == len(delta), "targetCacheCompleteAndUnique": True,
            "signatureIsolationExact": helper.source_pairs(census_rows) == delta,
            "baselineCollisionCountZero": not bool(delta & baseline),
            "priorFrozenSignatureCollisionCountZero": not bool(delta & prior_signature_pairs),
            "priorMapChainCompleteThroughV17": len(prior_artifacts) == 21,
            "outputAbsentGuardEnabled": True, "coefficientAndCredentialScanClean": True,
        },
        "artifacts": {
            "groupInput": helper.relative_artifact(GROUP_INPUT), "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY),
            "censusInput": helper.relative_artifact(CENSUS_INPUT), "exactSourceInventory": helper.relative_artifact(INVENTORY),
            "provenancePlan": helper.relative_artifact(PLAN), "preparer": helper.relative_artifact(Path(__file__).resolve()),
        },
        "workerPreflight": {"selectedRows": len(delta_labels), "selectedSignatures": len(delta), "signatureAware": True, "shardCount": 1, "checkpointEvery": 1, "outputAbsentAtSeal": not OUTPUT.exists(), "heavyCommandFrozen": bool(args.finalize_heavy_plan)},
        "coefficientMaterialIncluded": False, "credentialMaterialIncluded": False,
        "sideEffects": {"heavyWorkerLaunched": False, "networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0, "writesLimitedToV18Artifacts": True},
    }
    if not all(audit["checks"].values()):
        raise ValueError("v18 preflight check failed")
    prior.assert_coefficient_free(audit, "preflight")
    helper.atomic_json(PREFLIGHT, audit)
    print(json.dumps({
        "status": plan["status"], "baseOwnedPairs": len(v17_pairs), "snapshotOwnedPairs": len(accepted_pairs),
        "deltaPairs": len(delta), "selectedWorkerRows": len(delta_labels), "selectedWorkerSignatures": len(delta),
        "heavyCommandFrozen": bool(args.finalize_heavy_plan), "heavyWorkerLaunched": False,
        "plan": str(PLAN.relative_to(ROOT)), "preflight": str(PREFLIGHT.relative_to(ROOT)),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
