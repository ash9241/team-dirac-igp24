#!/usr/bin/env python3
"""Light-only immutable post-v20 pair-delta preparer.

This script never launches Sage/GAP or performs network, submission, or ledger
writes.  It pins the completed v20 boundary, isolates newly accepted-scoreable
pairs, validates their committed receipt provenance, and emits one guarded
serial census command for later root authorization.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import prepare_v11_pair_delta as helper
import prepare_v17_pair_delta as core


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
BASE = ROOT / "data/autopilot_pair_delta_20260722_v20"
DEST = ROOT / "data/autopilot_pair_delta_20260722_v21"
GROUP_INPUT = DEST / "group_input.jsonl"
GROUP_SUMMARY = DEST / "group_input_summary.json"
CENSUS_INPUT = DEST / "census_input.jsonl"
INVENTORY = DEST / "exact_source_inventory.json"
PLAN = DEST / "provenance_plan.json"
PREFLIGHT = DEST / "preflight_audit_summary.json"
OUTPUT = DEST / "missing_pair_all.jsonl"

BASE_ROWS = 13_346
BASE_PAIRS = 22_030
BASE_DELTA_ROWS = 2
BASE_DELTA_PAIRS = 2
TARGET_ROWS = 165_836
TARGET_LABELS = 25_000

VERIFIED_RECEIPTS = {
    "sub_f34764affb9f428b93cb5f3577001a73": 9,
    "sub_858b956217294d8cb269f12a098c2eb5": 2,
    "sub_9850254c45164663a2db09657e84d082": 11,
    "sub_c6f6ebb4f625451d9b552a523274b1f6": 9,
    "sub_aed2e90f4b804427b2d0f1e497e042ae": 1,
}

PINS = {
    "prepare_v20_pair_delta.py": "7ebc4387e0ba84a7d4f432955f72fa1c030a9f79281d232190cfcbe234f1d63d",
    "data/autopilot_pair_delta_20260722_v20/group_input.jsonl": "65d2953f02bde87f09c69102abe9be70edaa8a07fd79abd11fc75a14273f8531",
    "data/autopilot_pair_delta_20260722_v20/group_input_summary.json": "ab96ee60e0eba3e28474ac5df8822d1ce0250c5af567abe7a9ec3d35d7fcedb8",
    "data/autopilot_pair_delta_20260722_v20/census_input.jsonl": "da0f723dfdfd4374fbd931cd1801d594e090377521597250abbe43799bbbdfbf",
    "data/autopilot_pair_delta_20260722_v20/missing_pair_all.jsonl": "039b7298f6bbe64818d7e2fef2bfde9db458d652c9f16ec0d1d662d09f8ea381",
    "data/autopilot_pair_delta_20260722_v20/exact_source_inventory.json": "a53e19329affd21ff5127120a41e192d37cb0dde5aee6b838211a1af5e773e90",
    "data/autopilot_pair_delta_20260722_v20/provenance_plan.json": "2fdca69bd7e4c14659baa236f7bf1fcb638eab991359f7f06785f922b3fb328f",
    "data/autopilot_pair_delta_20260722_v20/preflight_audit_summary.json": "7381f3601838a22f297603c308a6ad1970ae4e507e9aac3b512d0354c2765529",
    "receipts/sub_f34764affb9f428b93cb5f3577001a73.json": "369067cafb00faa2e7d788c0aa9e26f665d0fa73e863629436f1405696ebaa98",
    "receipts/sub_858b956217294d8cb269f12a098c2eb5.json": "f38bbc9f2acf320813bd9c83315623689ddf44188371b974f474dafd1ec4882a",
    "receipts/sub_9850254c45164663a2db09657e84d082.json": "6ebe703d0f429e07a3d9016b731628f16f1c0de89dcd43c82d4b1a0490d023d8",
    "receipts/sub_c6f6ebb4f625451d9b552a523274b1f6.json": "13e3d90546bcc1b43dca1b4c9576a84629d0df33b18ccaa800ea467798f4d68e",
    "receipts/sub_aed2e90f4b804427b2d0f1e497e042ae.json": "7e726a0a32244c5b27b8d5ca9e163160292dd5d350c64e3fc1701ee43896ddb3",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalize-heavy-plan", action="store_true")
    parser.add_argument("--expected-delta-pairs", type=int)
    return parser.parse_args()


def require_pins() -> None:
    for relative, expected in PINS.items():
        path = ROOT / relative
        if not path.is_file() or helper.sha256_path(path) != expected:
            raise ValueError(f"pinned v20 boundary artifact changed: {relative}")


def validate_base() -> tuple[
    set[tuple[str, int]], list[dict], set[tuple[str, int]]
]:
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
        (str(row.get("sourceLabel")), int(r_value))
        for row in completed
        for r_value in row.get("sourceR") or []
    }
    inventory_pairs = {
        core.parsed_pair(value) for value in inventory.get("deltaPairs") or []
    }
    if (
        len(group) != BASE_ROWS
        or len(owned) != BASE_PAIRS
        or len(census) != BASE_ROWS
        or len(completed) != BASE_DELTA_ROWS
        or len(selected) != BASE_DELTA_PAIRS
        or selected != done
        or selected != inventory_pairs
        or sum(len(row.get("routes") or []) for row in completed) != 0
        or any(
            row.get("status") != "certified"
            or not helper.certificate_is_exact(row)
            for row in completed
        )
        or summary.get("status") != "certified_boundary_confirmed"
        or summary.get("heavyPlanFinalized") is not True
        or int(summary.get("snapshotOwnedPairs", -1)) != BASE_PAIRS
        or int(summary.get("deltaPairs", -1)) != BASE_DELTA_PAIRS
        or int(summary.get("deltaLabels", -1)) != BASE_DELTA_ROWS
        or inventory.get("status") != "certified"
        or int(inventory.get("deltaPairCount", -1)) != BASE_DELTA_PAIRS
        or set((inventory.get("acceptedAnchorsByPair") or {}))
        != {core.pair_text(pair) for pair in selected}
        or plan.get("status") != "ready_for_one_heavy_worker"
        or (plan.get("execution") or {}).get("heavyCommandFrozen") is not True
        or preflight.get("status") != "certified_safe_for_one_heavy_worker"
        or not all((preflight.get("checks") or {}).values())
    ):
        raise ValueError("v20 frozen two-row zero-route boundary changed")

    prior_maps = list((plan.get("artifacts") or {}).get("priorMaps") or [])
    if len(prior_maps) != 23:
        raise ValueError("v20 prior chain cardinality changed")
    prior_signatures: set[tuple[str, int]] = set()
    for artifact in prior_maps:
        path = ROOT / str(artifact["path"])
        if not path.is_file() or helper.sha256_path(path) != artifact["sha256"]:
            raise ValueError(f"prior map changed: {path}")
        for row in helper.read_jsonl(path):
            if row.get("sourceLabel") is not None:
                prior_signatures.update(
                    (str(row["sourceLabel"]), int(r_value))
                    for r_value in row.get("sourceR") or []
                )
    prior_maps.append(helper.relative_artifact(BASE / "missing_pair_all.jsonl"))
    prior_signatures.update(done)
    if not prior_signatures <= owned:
        raise ValueError("prior chain through v20 escapes frozen ownership")
    return owned, prior_maps, prior_signatures


def validate_receipts(
    connection: sqlite3.Connection,
) -> tuple[dict, set[tuple[str, int]]]:
    audits = {}
    receipt_pairs: set[tuple[str, int]] = set()
    for submission_id, expected in VERIFIED_RECEIPTS.items():
        receipt_path = ROOT / f"receipts/{submission_id}.json"
        receipt = helper.read_json(receipt_path)
        hashes = core.manifest_hashes(receipt, expected)
        submission = connection.execute(
            "SELECT queued_count,verified_count,failed_count,synced_at "
            "FROM submissions WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
        rows = list(
            connection.execute(
                "SELECT p.coefficient_hash,v.label,v.r,v.status,v.scoreable,"
                "v.scoring_status,v.in_baseline FROM polynomials p "
                "JOIN verifications v USING(submission_id,polynomial_index) "
                "WHERE p.submission_id=? ORDER BY p.polynomial_index",
                (submission_id,),
            )
        )
        pairs = {(str(row[1]), int(row[2])) for row in rows}
        if (
            submission is None
            or tuple(map(int, submission[:3])) != (0, expected, 0)
            or len(rows) != expected
            or {str(row[0]) for row in rows} != hashes
            or len(pairs) != expected
            or pairs & receipt_pairs
            or any(
                str(row[3]) != "accepted"
                or int(row[4]) != 1
                or str(row[5]) != "scoreable"
                or int(row[6]) != 0
                for row in rows
            )
        ):
            raise ValueError(f"verified v21 boundary receipt changed: {submission_id}")
        receipt_pairs.update(pairs)
        audits[submission_id] = {
            "receipt": helper.relative_artifact(receipt_path),
            "manifestSha256": str(receipt["manifestHash"]),
            "verifiedAcceptedScoreable": expected,
            "distinctPairs": len(pairs),
            "syncedAtEpoch": float(submission[3]),
            "pairSetSha256": helper.sha256_bytes(
                json.dumps(
                    sorted(
                        [list(pair) for pair in pairs],
                        key=lambda value: (int(value[0][3:]), value[1]),
                    ),
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
        }
    if len(receipt_pairs) != sum(VERIFIED_RECEIPTS.values()):
        raise ValueError("verified receipt pair union cardinality changed")
    return audits, receipt_pairs


def main() -> int:
    args = parse_args()
    if args.finalize_heavy_plan and args.expected_delta_pairs is None:
        raise ValueError("--expected-delta-pairs required")
    if OUTPUT.exists() or OUTPUT.with_suffix(OUTPUT.suffix + ".tmp").exists():
        raise FileExistsError(f"refusing to overwrite heavy output: {OUTPUT}")

    base_pairs, prior_maps, prior_signatures = validate_base()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        receipts, receipt_pairs = validate_receipts(connection)
        baseline = {
            (str(label), int(r_value))
            for label, r_value in connection.execute(
                "SELECT label,r FROM baseline_pairs"
            )
        }
        accepted = {
            (str(label), int(r_value))
            for label, r_value in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE status='accepted' AND scoreable=1"
            )
        }
        if not base_pairs <= accepted:
            raise ValueError("ledger regressed behind v20")
        delta = accepted - base_pairs
        if (
            not delta
            or delta & baseline
            or delta & prior_signatures
            or delta != receipt_pairs
        ):
            raise ValueError("post-v20 accepted delta is not the exact receipt union")
        if args.expected_delta_pairs is not None and len(delta) != args.expected_delta_pairs:
            raise ValueError(
                f"expected {args.expected_delta_pairs} pairs, found {len(delta)}"
            )
        anchors = core.accepted_anchors(connection, delta)
        targets = [
            (
                str(label),
                int(r_value),
                int(team_count),
                bool(discovered),
                str(minimum_disc) if minimum_disc is not None else None,
                str(generated_at),
            )
            for label, r_value, team_count, discovered, minimum_disc, generated_at
            in connection.execute(
                "SELECT label,r,team_count,discovered,minimum_disc_abs,generated_at "
                "FROM targets"
            )
        ]
        accepted_anchor_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM verifications "
                "WHERE status='accepted' AND scoreable=1"
            ).fetchone()[0]
        )
        latest_sync = connection.execute(
            "SELECT MAX(synced_at) FROM submissions"
        ).fetchone()[0]
        queued = [
            {"submissionId": str(row[0]), "queuedCount": int(row[1])}
            for row in connection.execute(
                "SELECT submission_id,queued_count FROM submissions "
                "WHERE queued_count>0 ORDER BY submission_id"
            )
        ]
    finally:
        connection.close()

    target_map = {(row[0], row[1]): row[2:] for row in targets}
    if (
        len(target_map) != TARGET_ROWS
        or len({row[0] for row in targets}) != TARGET_LABELS
        or not delta <= set(target_map)
    ):
        raise ValueError("target cache incomplete")

    owned_by_label: dict[str, set[int]] = {}
    for label, r_value in accepted:
        owned_by_label.setdefault(label, set()).add(r_value)
    gold_by_label: dict[str, set[int]] = {}
    for pair, state in target_map.items():
        if state[0] == 0 and pair not in baseline and pair not in accepted:
            gold_by_label.setdefault(pair[0], set()).add(pair[1])
    labels = sorted(
        set(owned_by_label) | set(gold_by_label), key=lambda value: int(value[3:])
    )
    group = [
        {
            "goldR": sorted(gold_by_label.get(label, set())),
            "isGoldTarget": label in gold_by_label,
            "isOwnedSource": label in owned_by_label,
            "label": label,
            "sourceR": sorted(owned_by_label.get(label, set())),
            "t": int(label[3:]),
        }
        for label in labels
    ]
    if helper.source_pairs(group) != accepted:
        raise ValueError("v21 group ownership mismatch")
    census = []
    for row in group:
        selected = sorted(r for label, r in delta if label == row["label"])
        census.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if helper.source_pairs(census) != delta:
        raise ValueError("v21 delta isolation mismatch")

    sorted_delta = sorted(delta, key=core.pair_key)
    delta_labels = {label for label, _r in delta}
    boundary = {
        "groupInput": helper.relative_artifact(BASE / "group_input.jsonl"),
        "censusInput": helper.relative_artifact(BASE / "census_input.jsonl"),
        "completedCensus": helper.relative_artifact(BASE / "missing_pair_all.jsonl"),
        "preflight": helper.relative_artifact(BASE / "preflight_audit_summary.json"),
    }
    summary = {
        "schemaVersion": "v21-immutable-group-input-summary-v1",
        "status": (
            "certified_boundary_confirmed"
            if args.finalize_heavy_plan
            else "prepared_awaiting_boundary_confirmation"
        ),
        "base": boundary,
        "groupInput": {"path": str(GROUP_INPUT.relative_to(ROOT))},
        "rows": len(group),
        "baseOwnedPairs": len(base_pairs),
        "snapshotOwnedPairs": len(accepted),
        "deltaPairs": len(delta),
        "deltaLabels": len(delta_labels),
        "targetLabels": TARGET_LABELS,
        "targetRows": TARGET_ROWS,
        "targetGeneratedAtMin": min(row[5] for row in targets),
        "targetGeneratedAtMax": max(row[5] for row in targets),
        "heavyPlanFinalized": bool(args.finalize_heavy_plan),
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    inventory = {
        "schemaVersion": "v21-accepted-scoreable-source-inventory-v1",
        "status": "certified",
        "base": boundary,
        "receiptBoundary": receipts,
        "queuedSubmissionExclusion": queued,
        "ledgerSnapshot": {
            "acceptedScoreablePairs": len(accepted),
            "acceptedScoreableAnchorRows": accepted_anchor_rows,
            "latestSubmissionSyncEpoch": latest_sync,
        },
        "deltaPairCount": len(delta),
        "deltaLabelCount": len(delta_labels),
        "deltaPairs": [core.pair_text(pair) for pair in sorted_delta],
        "acceptedAnchorsByPair": anchors,
        "targetSnapshot": [
            {
                "pair": core.pair_text(pair),
                "teamCount": target_map[pair][0],
                "discovered": target_map[pair][1],
                "minimumDiscAbs": target_map[pair][2],
                "generatedAt": target_map[pair][3],
            }
            for pair in sorted_delta
        ],
        "collisionCensus": {
            "baselinePairCollisions": len(delta & baseline),
            "priorFrozenSignatureCollisions": len(delta & prior_signatures),
            "unanchoredDeltaPairs": len(delta) - len(anchors),
            "receiptUnionSymmetricDifference": len(delta ^ receipt_pairs),
        },
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    for value, name in (
        (group, "group"),
        (census, "census"),
        (summary, "summary"),
        (inventory, "inventory"),
    ):
        core.assert_coefficient_free(value, name)

    helper.atomic_text(GROUP_INPUT, helper.canonical_jsonl(group))
    helper.atomic_text(CENSUS_INPUT, helper.canonical_jsonl(census))
    summary["groupInput"]["sha256"] = helper.sha256_path(GROUP_INPUT)
    helper.atomic_json(GROUP_SUMMARY, summary)
    helper.atomic_json(INVENTORY, inventory)

    argv = [
        "/usr/bin/caffeinate",
        "-i",
        "/usr/local/bin/sage",
        "-python",
        "agent_index24_missing_pair_census.sage.py",
        "--input",
        str(CENSUS_INPUT.relative_to(ROOT)),
    ]
    for artifact in prior_maps:
        argv.extend(["--prior-map", artifact["path"]])
    argv.extend(
        [
            "--signature-aware",
            "--prior-input",
            "data/autopilot_pair_delta_20260722_v20/group_input.jsonl",
            "--output",
            str(OUTPUT.relative_to(ROOT)),
            "--shard-index",
            "0",
            "--shard-count",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )
    guarded = None
    if args.finalize_heavy_plan:
        output = shlex.quote(str(OUTPUT.relative_to(ROOT)))
        temporary = shlex.quote(str(OUTPUT.relative_to(ROOT)) + ".tmp")
        guarded = [
            "/bin/sh",
            "-c",
            f"test ! -e {output} && test ! -e {temporary} && exec {shlex.join(argv)}",
        ]
    plan = {
        "schemaVersion": "v21-pair-delta-provenance-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "ready_for_one_heavy_worker"
            if args.finalize_heavy_plan
            else "prepared_awaiting_boundary_confirmation"
        ),
        "artifacts": {
            "groupInput": helper.relative_artifact(GROUP_INPUT),
            "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY),
            "censusInput": helper.relative_artifact(CENSUS_INPUT),
            "exactSourceInventory": helper.relative_artifact(INVENTORY),
            "preparer": helper.relative_artifact(Path(__file__).resolve()),
            "priorMaps": prior_maps,
            "worker": helper.relative_artifact(
                ROOT / "agent_index24_missing_pair_census.sage.py"
            ),
            "base": boundary,
        },
        "delta": {
            "selectedLabels": len(delta_labels),
            "selectedSignatures": len(delta),
            "pairs": [core.pair_text(pair) for pair in sorted_delta],
            "signatureBaseline": (
                "completed pinned v20 boundary, 2/2 labels, 2/2 signatures, "
                "zero routes"
            ),
        },
        "execution": {
            "heavyWorkerLaunched": False,
            "heavyCommandFrozen": bool(args.finalize_heavy_plan),
            "guardedPlannedCommand": guarded,
            "maximumHeavyConcurrency": 1,
            "outputAbsentAtPreparation": True,
            "outputAndTemporaryAbsentGuardRequired": True,
            "requiresRootHeavyWorkerClearance": True,
            "requiresAcceptedBoundaryConfirmation": not args.finalize_heavy_plan,
            "expectedDeltaPairsAtFinalization": args.expected_delta_pairs,
        },
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    core.assert_coefficient_free(plan, "plan")
    helper.atomic_json(PLAN, plan)

    audit = {
        "schemaVersion": "v21-light-preflight-audit-v1",
        "sealedAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_safe_for_one_heavy_worker"
            if args.finalize_heavy_plan
            else "certified_light_snapshot_awaiting_boundary_confirmation"
        ),
        "base": {
            "version": "v20",
            "frozenOwnedPairs": len(base_pairs),
            "completedCensusRows": BASE_DELTA_ROWS,
            "conditionalRoutes": 0,
            "artifacts": boundary,
        },
        "receiptBoundary": receipts,
        "queuedSubmissionExclusion": queued,
        "snapshot": {
            "acceptedScoreablePairs": len(accepted),
            "deltaPairs": len(delta),
            "deltaLabels": len(delta_labels),
            "groupRows": len(group),
            "targetLabels": TARGET_LABELS,
            "targetRows": TARGET_ROWS,
        },
        "checks": {
            "v20BoundaryPinned": True,
            "v20CompletedCensusExactZeroRoutes": True,
            "fiveVerifiedReceiptBoundaryPinned": len(receipts) == 5,
            "verifiedReceiptPairUnionEqualsAcceptedDelta": receipt_pairs == delta,
            "acceptedScoreableDeltaExact": True,
            "provenanceAnchorsComplete": len(anchors) == len(delta),
            "targetCacheCompleteAndUnique": True,
            "signatureIsolationExact": helper.source_pairs(census) == delta,
            "baselineCollisionCountZero": not bool(delta & baseline),
            "priorFrozenSignatureCollisionCountZero": not bool(
                delta & prior_signatures
            ),
            "priorMapChainCompleteThroughV20": len(prior_maps) == 24,
            "outputAndTemporaryAbsentGuardEnabled": True,
            "coefficientAndCredentialScanClean": True,
        },
        "artifacts": {
            "groupInput": helper.relative_artifact(GROUP_INPUT),
            "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY),
            "censusInput": helper.relative_artifact(CENSUS_INPUT),
            "exactSourceInventory": helper.relative_artifact(INVENTORY),
            "provenancePlan": helper.relative_artifact(PLAN),
            "preparer": helper.relative_artifact(Path(__file__).resolve()),
        },
        "workerPreflight": {
            "selectedRows": len(delta_labels),
            "selectedSignatures": len(delta),
            "signatureAware": True,
            "shardCount": 1,
            "checkpointEvery": 1,
            "maximumHeavyConcurrency": 1,
            "outputAbsentAtSeal": not OUTPUT.exists(),
            "temporaryAbsentAtSeal": not OUTPUT.with_suffix(
                OUTPUT.suffix + ".tmp"
            ).exists(),
            "heavyCommandFrozen": bool(args.finalize_heavy_plan),
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "heavyWorkerLaunched": False,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "writesLimitedToV21Artifacts": True,
            "batch2ArtifactsTouched": False,
        },
    }
    if not all(audit["checks"].values()):
        raise ValueError("v21 preflight failed")
    core.assert_coefficient_free(audit, "preflight")
    helper.atomic_json(PREFLIGHT, audit)
    print(
        json.dumps(
            {
                "status": plan["status"],
                "baseOwnedPairs": len(base_pairs),
                "snapshotOwnedPairs": len(accepted),
                "deltaPairs": len(delta),
                "selectedWorkerRows": len(delta_labels),
                "selectedWorkerSignatures": len(delta),
                "heavyCommandFrozen": bool(args.finalize_heavy_plan),
                "heavyWorkerLaunched": False,
                "plan": str(PLAN.relative_to(ROOT)),
                "preflight": str(PREFLIGHT.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
