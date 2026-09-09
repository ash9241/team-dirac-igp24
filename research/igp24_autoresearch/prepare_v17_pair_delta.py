#!/usr/bin/env python3
"""Prepare the coefficient-free accepted-scoreable post-v16 pair delta.

This follows the v16 two-phase guard pattern.  The default phase refreshes a
light snapshot and freezes exact accepted anchors/provenance without a heavy
command.  ``--finalize-heavy-plan`` additionally freezes one guarded census
command after an exact delta-cardinality check.  Neither phase executes Sage,
GAP, network operations, submissions, or ledger writes.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import prepare_v11_pair_delta as helper
import stage_single_exact_census as exact_census


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V16 = ROOT / "data/autopilot_pair_delta_20260722_v16"
V17 = ROOT / "data/autopilot_pair_delta_20260722_v17"
GROUP_INPUT = V17 / "group_input.jsonl"
GROUP_SUMMARY = V17 / "group_input_summary.json"
CENSUS_INPUT = V17 / "census_input.jsonl"
INVENTORY = V17 / "exact_source_inventory.json"
PLAN = V17 / "provenance_plan.json"
PREFLIGHT = V17 / "preflight_audit_summary.json"
OUTPUT = V17 / "missing_pair_all.jsonl"

EXPECTED_TARGET_LABELS = 25_000
EXPECTED_TARGET_ROWS = 165_836
EXPECTED_V16_GROUP_ROWS = 13_325
EXPECTED_V16_OWNED_PAIRS = 21_956
EXPECTED_V16_DELTA_LABELS = 14
EXPECTED_V16_DELTA_PAIRS = 14
EXPECTED_V16_CONDITIONAL_ROUTES = 3

V16_PINS = {
    "prepare_v16_pair_delta.py":
        "489e0d181e6cea08de9e845b53ca9cc60c81265c52a62e672f4b001ab6604bf7",
    "data/autopilot_pair_delta_20260722_v16/group_input.jsonl":
        "e37b14662d53583cd9bd9bd5388fa2893554d29b7001c501123887561542b051",
    "data/autopilot_pair_delta_20260722_v16/group_input_summary.json":
        "36b709c409de2dd4cb6314d6d7a3527554c1a0d3c4076f0cc9b3d08d6c9c42e1",
    "data/autopilot_pair_delta_20260722_v16/census_input.jsonl":
        "b4ed387e06357a92085b13a8dbb20a51b194ed69821a30dfe0b2cc14ebd58aaf",
    "data/autopilot_pair_delta_20260722_v16/missing_pair_all.jsonl":
        "b5db04734c30d252ef8208c25851633f65d61ebc04175e5285c05f39440483f1",
    "data/autopilot_pair_delta_20260722_v16/exact_source_inventory.json":
        "3ec388bec9094ee26656ba4bbd5175297708b8e7663f71172824041323d3ce5e",
    "data/autopilot_pair_delta_20260722_v16/provenance_plan.json":
        "5e43360642eaa6ac0a1a2f8d9882f1134bfe96a33868a86429eb06829711d68a",
    "data/autopilot_pair_delta_20260722_v16/preflight_audit_summary.json":
        "2ffdb25a4a6dc7bde31702724b2bcbd1154ded9a76cecff383cf26d0419c4f03",
    "data/v16_gold_pair_resolvent_run_certificate.json":
        "5fa819cdb46170aea8fc13d1a47b9d506fe79c9e299541232283de2ddcc54af7",
    "data/v16_gold_pair_resolvent_candidates.jsonl":
        "d2d097ca894fb256a66719466a422f43132ab1269a3e0149aeaa73476e091ea3",
    "data/v16_gold_pair_resolvent_frobenius_certificate.json":
        "7ca42d4f5781f15cee6c5ff4ce06a574ceaa4d9484a3b5dcdfd4ec90ad93006c",
    "data/v16_gold_pair_resolvent_stage_summary.json":
        "17851309084dbe9941031cd1573f498c69e2403511ab897f2490697cf295eb7f",
    "receipts/sub_a1115b5e820c47c59e3be632d1b673b5.json":
        "591727fa27f306bd8af60582beada180443b5fad645c045459604c1eeb33c83b",
    "receipts/sub_b4f7f2bd853446aca3382d22345a73ec.json":
        "56c308335525e98257af7c671833fcad90007a37d86ff5206b64eab3dc45ce02",
    "data/low_contention_tc4_receipt_mapping_ready.json":
        "eb69341106470296130a608b3ed7c85d0be1addb55c2d6ca0e4e78588d153147",
}

RECEIPT_BOUNDARY = {
    "sub_a1115b5e820c47c59e3be632d1b673b5": 2,
    "sub_b4f7f2bd853446aca3382d22345a73ec": 22,
}
QUEUED_FOR_V18 = {
    "sub_6eeaa15d8d994fd19646243de90b4b09": 3,
    "sub_bc7a3dd9294543bd813bed8ba413972b": 14,
}

PAIR_RE = re.compile(
    r"(24T[1-9][0-9]*):(0|2|4|6|8|10|12|14|16|18|20|22|24)\Z"
)
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
COEFFICIENT_LINE_RE = re.compile(
    r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalize-heavy-plan", action="store_true")
    parser.add_argument("--expected-delta-pairs", type=int)
    return parser.parse_args()


def pair_key(pair: tuple[str, int]) -> tuple[int, int]:
    return int(pair[0][3:]), int(pair[1])


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}:{pair[1]}"


def parsed_pair(value: object) -> tuple[str, int]:
    match = PAIR_RE.fullmatch(str(value))
    if match is None:
        raise ValueError(f"invalid pair string: {value!r}")
    return match.group(1), int(match.group(2))


def require_pin(relative: str) -> None:
    path = ROOT / relative
    if not path.is_file() or helper.sha256_path(path) != V16_PINS[relative]:
        raise ValueError(f"pinned v16 artifact changed: {relative}")


def assert_coefficient_free(value: object, name: str) -> None:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if COEFFICIENT_LINE_RE.search(encoded):
        raise ValueError(f"{name} contains a degree-24 coefficient line")
    lowered = encoded.lower()
    if '"coefficients"' in lowered or '"originalline"' in lowered:
        raise ValueError(f"{name} contains raw coefficient material")
    if "bearer " in lowered or '"authorization"' in lowered:
        raise ValueError(f"{name} contains credential material")


def validate_v16_boundary() -> tuple[
    list[dict], set[tuple[str, int]], list[dict], set[tuple[str, int]]
]:
    for relative in V16_PINS:
        require_pin(relative)

    group_rows = helper.read_jsonl(V16 / "group_input.jsonl")
    census_rows = helper.read_jsonl(V16 / "census_input.jsonl")
    completed_rows = helper.read_jsonl(V16 / "missing_pair_all.jsonl")
    summary = helper.read_json(V16 / "group_input_summary.json")
    inventory = helper.read_json(V16 / "exact_source_inventory.json")
    plan = helper.read_json(V16 / "provenance_plan.json")
    preflight = helper.read_json(V16 / "preflight_audit_summary.json")

    owned_pairs = helper.source_pairs(group_rows)
    selected_pairs = helper.source_pairs(census_rows)
    completed_pairs = {
        (str(row.get("sourceLabel")), int(r))
        for row in completed_rows
        for r in row.get("sourceR") or []
    }
    inventory_pairs = {
        parsed_pair(value) for value in inventory.get("deltaPairs") or []
    }
    route_count = sum(len(row.get("routes") or []) for row in completed_rows)
    if (
        len(group_rows) != EXPECTED_V16_GROUP_ROWS
        or len(owned_pairs) != EXPECTED_V16_OWNED_PAIRS
        or len(census_rows) != EXPECTED_V16_GROUP_ROWS
        or len(completed_rows) != EXPECTED_V16_DELTA_LABELS
        or len(selected_pairs) != EXPECTED_V16_DELTA_PAIRS
        or selected_pairs != completed_pairs
        or selected_pairs != inventory_pairs
        or route_count != EXPECTED_V16_CONDITIONAL_ROUTES
        or any(
            row.get("status") != "certified"
            or not helper.certificate_is_exact(row)
            for row in completed_rows
        )
    ):
        raise ValueError("pinned v16 census boundary is incomplete")

    if (
        summary.get("schemaVersion")
        != "v16-refreshable-group-input-summary-v1"
        or summary.get("status") != "certified_boundary_confirmed"
        or summary.get("heavyPlanFinalized") is not True
        or int(summary.get("baseOwnedPairs", -1)) != 21_942
        or int(summary.get("snapshotOwnedPairs", -1))
        != EXPECTED_V16_OWNED_PAIRS
        or int(summary.get("deltaPairs", -1)) != EXPECTED_V16_DELTA_PAIRS
        or int(summary.get("deltaLabels", -1))
        != EXPECTED_V16_DELTA_LABELS
    ):
        raise ValueError("v16 group summary envelope changed")
    if (
        inventory.get("status") != "certified"
        or int(inventory.get("deltaPairCount", -1))
        != EXPECTED_V16_DELTA_PAIRS
        or set((inventory.get("acceptedAnchorsByPair") or {}))
        != {pair_text(pair) for pair in selected_pairs}
    ):
        raise ValueError("v16 exact-source inventory changed")
    if (
        plan.get("status") != "ready_for_one_heavy_worker"
        or (plan.get("execution") or {}).get("heavyCommandFrozen") is not True
        or int((plan.get("delta") or {}).get("selectedSignatures", -1))
        != EXPECTED_V16_DELTA_PAIRS
    ):
        raise ValueError("v16 provenance plan changed")
    if (
        preflight.get("status") != "certified_safe_for_one_heavy_worker"
        or not all((preflight.get("checks") or {}).values())
        or int((preflight.get("snapshot") or {}).get("deltaPairs", -1))
        != EXPECTED_V16_DELTA_PAIRS
    ):
        raise ValueError("v16 preflight is not fully certified")

    prior_artifacts = list((plan.get("artifacts") or {}).get("priorMaps") or [])
    if len(prior_artifacts) != 19:
        raise ValueError("v16 prior-map chain cardinality changed")
    prior_signature_pairs = set()
    for artifact in prior_artifacts:
        path = ROOT / str(artifact["path"])
        if (
            not path.is_file()
            or helper.sha256_path(path) != str(artifact["sha256"])
        ):
            raise ValueError(f"v16 prior map changed: {path}")
        for row in helper.read_jsonl(path):
            if row.get("sourceLabel") is not None:
                prior_signature_pairs.update(
                    (str(row["sourceLabel"]), int(r))
                    for r in row.get("sourceR") or []
                )
    terminal = helper.relative_artifact(V16 / "missing_pair_all.jsonl")
    prior_artifacts.append(terminal)
    prior_signature_pairs.update(completed_pairs)
    if prior_signature_pairs != owned_pairs:
        # The map chain need not cover old source pairs with no length-24
        # action, but it must remain wholly inside the frozen boundary.
        if not prior_signature_pairs <= owned_pairs:
            raise ValueError("v16 prior-map chain escapes frozen ownership")

    frobenius = helper.read_json(
        ROOT / "data/v16_gold_pair_resolvent_frobenius_certificate.json"
    )
    frobenius_summary = frobenius.get("summary") or {}
    if (
        frobenius.get("selectedInputRowsSha256")
        != V16_PINS["data/v16_gold_pair_resolvent_candidates.jsonl"]
        or int(frobenius_summary.get("rows", -1)) != 2
        or int(frobenius_summary.get("resolved", -1)) != 2
        or int(frobenius_summary.get("unresolved", -1)) != 0
        or int(frobenius_summary.get("contradiction", -1)) != 0
    ):
        raise ValueError("v16 gold Frobenius closure is not exact")
    return group_rows, owned_pairs, prior_artifacts, prior_signature_pairs


def manifest_hashes(receipt: dict, expected: int) -> set[str]:
    manifest = Path(str(receipt["manifest"])).resolve()
    if (
        not manifest.is_file()
        or exact_census.sha256_path(manifest)
        != str(receipt["manifestHash"])
    ):
        raise ValueError("boundary receipt manifest changed")
    hashes = set()
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        line = exact_census.canonical_polynomial_line(raw_line.strip())
        if line is not None:
            hashes.add(exact_census.sha256_bytes(line.encode("ascii")))
    if len(hashes) != expected:
        raise ValueError("boundary receipt manifest cardinality changed")
    return hashes


def validate_receipt_boundary(connection: sqlite3.Connection) -> dict:
    result = {}
    for submission_id, expected in RECEIPT_BOUNDARY.items():
        receipt = helper.read_json(ROOT / f"receipts/{submission_id}.json")
        receipt_hashes = manifest_hashes(receipt, expected)
        submission = connection.execute(
            "SELECT queued_count,verified_count,failed_count,synced_at "
            "FROM submissions WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
        if submission is None or tuple(map(int, submission[:3])) != (
            0,
            expected,
            0,
        ):
            raise ValueError(f"boundary receipt is not verified: {submission_id}")
        rows = list(connection.execute(
            "SELECT p.coefficient_hash,v.label,v.r,v.status,v.scoreable,"
            "v.scoring_status,v.in_baseline FROM polynomials p "
            "JOIN verifications v USING(submission_id,polynomial_index) "
            "WHERE p.submission_id=? ORDER BY p.polynomial_index",
            (submission_id,),
        ))
        if (
            len(rows) != expected
            or {str(row[0]) for row in rows} != receipt_hashes
            or any(
                str(row[3]) != "accepted"
                or int(row[4]) != 1
                or str(row[5]) != "scoreable"
                or int(row[6]) != 0
                for row in rows
            )
        ):
            raise ValueError(f"boundary receipt ledger rows changed: {submission_id}")
        pairs = {(str(row[1]), int(row[2])) for row in rows}
        if len(pairs) != expected:
            raise ValueError(f"boundary receipt pairs are not distinct: {submission_id}")
        result[submission_id] = {
            "receipt": helper.relative_artifact(
                ROOT / f"receipts/{submission_id}.json"
            ),
            "manifestSha256": str(receipt["manifestHash"]),
            "verifiedAcceptedScoreable": expected,
            "distinctPairs": len(pairs),
            "syncedAtEpoch": float(submission[3]),
            "pairSetSha256": helper.sha256_bytes(
                json.dumps(
                    sorted([list(pair) for pair in pairs], key=lambda x: (int(x[0][3:]), x[1])),
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
        }

    expected_a1115 = {("24T17570", 24), ("24T17796", 24)}
    actual_a1115 = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT label,r FROM verifications WHERE submission_id=?",
            ("sub_a1115b5e820c47c59e3be632d1b673b5",),
        )
    }
    if actual_a1115 != expected_a1115:
        raise ValueError("v16 gold receipt pair closure changed")

    tc4_mapping = helper.read_json(
        ROOT / "data/low_contention_tc4_receipt_mapping_ready.json"
    )
    expected_tc4_pairs = {
        parsed_pair(str(row["targetPair"]).replace("/r", ":"))
        for row in tc4_mapping.get("mappings") or []
    }
    actual_tc4_pairs = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT label,r FROM verifications WHERE submission_id=?",
            ("sub_b4f7f2bd853446aca3382d22345a73ec",),
        )
    }
    if len(expected_tc4_pairs) != 22 or actual_tc4_pairs != expected_tc4_pairs:
        raise ValueError("tc4 receipt pair mapping changed")

    for submission_id in QUEUED_FOR_V18:
        verification_count = int(connection.execute(
            "SELECT COUNT(*) FROM verifications WHERE submission_id=?",
            (submission_id,),
        ).fetchone()[0])
        submission = connection.execute(
            "SELECT verified_count,failed_count FROM submissions "
            "WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
        if verification_count or (
            submission is not None and (int(submission[0]) or int(submission[1]))
        ):
            raise ValueError(
                f"queued v18 receipt entered v17 boundary: {submission_id}"
            )
    return result


def accepted_anchors(
    connection: sqlite3.Connection, delta: set[tuple[str, int]]
) -> dict[str, list[dict]]:
    result = {}
    for label, r in sorted(delta, key=pair_key):
        anchors = []
        for row in connection.execute(
            "SELECT v.submission_id,v.polynomial_index,p.coefficient_hash,"
            "v.scoring_status,v.in_baseline,v.baseline_unlocked "
            "FROM verifications v JOIN polynomials p "
            "USING(submission_id,polynomial_index) "
            "WHERE v.label=? AND v.r=? AND v.status='accepted' "
            "AND v.scoreable=1 ORDER BY v.submission_id,v.polynomial_index",
            (label, r),
        ):
            digest = str(row[2])
            if (
                HASH_RE.fullmatch(digest) is None
                or str(row[3]) != "scoreable"
                or bool(row[4])
            ):
                raise ValueError(f"invalid accepted anchor for {label}:{r}")
            anchors.append({
                "submissionId": str(row[0]),
                "polynomialIndex": int(row[1]),
                "coefficientSha256": digest,
                "scoringStatus": str(row[3]),
                "inBaseline": bool(row[4]),
                "baselineUnlocked": bool(row[5]),
            })
        if not anchors:
            raise ValueError(f"unanchored post-v16 pair: {label}:{r}")
        result[pair_text((label, r))] = anchors
    return result


def main() -> int:
    args = parse_args()
    if args.finalize_heavy_plan and args.expected_delta_pairs is None:
        raise ValueError("--expected-delta-pairs required for finalization")
    if args.expected_delta_pairs is not None and args.expected_delta_pairs <= 0:
        raise ValueError("--expected-delta-pairs must be positive")
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite heavy output: {OUTPUT}")

    _, v16_pairs, prior_artifacts, prior_signature_pairs = (
        validate_v16_boundary()
    )
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        receipt_boundary = validate_receipt_boundary(connection)
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
        if not v16_pairs <= accepted_pairs:
            raise ValueError("ledger regressed behind sealed v16 boundary")
        delta = accepted_pairs - v16_pairs
        if not delta or delta & baseline or delta & prior_signature_pairs:
            raise ValueError("post-v16 delta is empty or collides with frozen state")
        if args.expected_delta_pairs is not None and len(delta) != args.expected_delta_pairs:
            raise ValueError(
                f"expected {args.expected_delta_pairs} delta pairs, found {len(delta)}"
            )
        anchors_by_pair = accepted_anchors(connection, delta)
        accepted_anchor_rows = int(connection.execute(
            "SELECT COUNT(*) FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        ).fetchone()[0])
        target_records = [
            (
                str(label), int(r), int(tc), bool(discovered),
                str(disc) if disc is not None else None, str(stamp),
            )
            for label, r, tc, discovered, disc, stamp in connection.execute(
                "SELECT label,r,team_count,discovered,minimum_disc_abs,generated_at "
                "FROM targets"
            )
        ]
        latest_sync = connection.execute(
            "SELECT MAX(synced_at) FROM submissions"
        ).fetchone()[0]
    finally:
        connection.close()

    target_map = {}
    target_labels = set()
    for label, r, tc, discovered, disc, stamp in target_records:
        pair = (label, r)
        if pair in target_map:
            raise ValueError(f"duplicate target pair: {pair_text(pair)}")
        target_map[pair] = (tc, discovered, disc, stamp)
        target_labels.add(label)
    if (
        len(target_map) != EXPECTED_TARGET_ROWS
        or len(target_labels) != EXPECTED_TARGET_LABELS
        or not delta <= set(target_map)
    ):
        raise ValueError("target snapshot is incomplete or misses delta pairs")

    owned_by_label: dict[str, set[int]] = {}
    for label, r in accepted_pairs:
        owned_by_label.setdefault(label, set()).add(r)
    gold_by_label: dict[str, set[int]] = {}
    for pair, state in target_map.items():
        if state[0] == 0 and pair not in baseline and pair not in accepted_pairs:
            gold_by_label.setdefault(pair[0], set()).add(pair[1])
    labels = sorted(
        set(owned_by_label) | set(gold_by_label), key=lambda value: int(value[3:])
    )
    group_rows = [
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
    if helper.source_pairs(group_rows) != accepted_pairs:
        raise ValueError("v17 group input does not reproduce current ownership")
    census_rows = []
    for row in group_rows:
        selected = sorted(r for label, r in delta if label == row["label"])
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if helper.source_pairs(census_rows) != delta:
        raise ValueError("v17 census does not isolate exact post-v16 delta")

    sorted_delta = sorted(delta, key=pair_key)
    delta_labels = {label for label, _ in delta}
    delta_snapshot = [
        {
            "pair": pair_text(pair),
            "teamCount": target_map[pair][0],
            "discovered": target_map[pair][1],
            "minimumDiscAbs": target_map[pair][2],
            "generatedAt": target_map[pair][3],
        }
        for pair in sorted_delta
    ]
    boundary_artifacts = {
        "groupInput": helper.relative_artifact(V16 / "group_input.jsonl"),
        "censusInput": helper.relative_artifact(V16 / "census_input.jsonl"),
        "completedCensus": helper.relative_artifact(V16 / "missing_pair_all.jsonl"),
        "preflight": helper.relative_artifact(V16 / "preflight_audit_summary.json"),
        "goldFrobeniusClosure": helper.relative_artifact(
            ROOT / "data/v16_gold_pair_resolvent_frobenius_certificate.json"
        ),
    }

    group_text = helper.canonical_jsonl(group_rows)
    census_text = helper.canonical_jsonl(census_rows)
    group_summary = {
        "schemaVersion": "v17-refreshable-group-input-summary-v1",
        "status": "certified_boundary_confirmed" if args.finalize_heavy_plan else "prepared_awaiting_boundary_confirmation",
        "base": boundary_artifacts,
        "groupInput": {"path": str(GROUP_INPUT.relative_to(ROOT))},
        "rows": len(group_rows),
        "baseOwnedPairs": len(v16_pairs),
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
        "schemaVersion": "v17-accepted-scoreable-source-inventory-v1",
        "status": "certified",
        "base": boundary_artifacts,
        "receiptBoundary": receipt_boundary,
        "queuedForV18Excluded": dict(QUEUED_FOR_V18),
        "ledgerSnapshot": {
            "acceptedScoreablePairs": len(accepted_pairs),
            "acceptedScoreableAnchorRows": accepted_anchor_rows,
            "latestSubmissionSyncEpoch": latest_sync,
        },
        "deltaPairCount": len(delta),
        "deltaLabelCount": len(delta_labels),
        "deltaPairs": [pair_text(pair) for pair in sorted_delta],
        "acceptedAnchorsByPair": anchors_by_pair,
        "targetSnapshot": delta_snapshot,
        "collisionCensus": {
            "baselinePairCollisions": len(delta & baseline),
            "priorFrozenSignatureCollisions": len(delta & prior_signature_pairs),
            "unanchoredDeltaPairs": len(delta) - len(anchors_by_pair),
        },
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    for value, name in (
        (group_rows, "group input"), (census_rows, "census input"),
        (group_summary, "group summary"), (inventory, "inventory"),
    ):
        assert_coefficient_free(value, name)
    helper.atomic_text(GROUP_INPUT, group_text)
    helper.atomic_text(CENSUS_INPUT, census_text)
    group_summary["groupInput"]["sha256"] = helper.sha256_path(GROUP_INPUT)
    helper.atomic_json(GROUP_SUMMARY, group_summary)
    helper.atomic_json(INVENTORY, inventory)

    worker_argv = [
        "caffeinate", "-i", "sage", "-python",
        "agent_index24_missing_pair_census.sage.py",
        "--input", str(CENSUS_INPUT.relative_to(ROOT)),
    ]
    for artifact in prior_artifacts:
        worker_argv.extend(["--prior-map", str(artifact["path"])])
    worker_argv.extend([
        "--signature-aware",
        "--prior-input", "data/autopilot_pair_delta_20260722_v16/group_input.jsonl",
        "--output", str(OUTPUT.relative_to(ROOT)),
        "--shard-index", "0", "--shard-count", "1",
        "--checkpoint-every", "1",
    ])
    guarded_command = None
    if args.finalize_heavy_plan:
        output_relative = str(OUTPUT.relative_to(ROOT))
        guarded_command = [
            "/bin/sh", "-c",
            f"test ! -e {shlex.quote(output_relative)} && exec {shlex.join(worker_argv)}",
        ]
    plan = {
        "schemaVersion": "v17-pair-delta-provenance-plan-v1",
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
            "base": boundary_artifacts,
        },
        "delta": {
            "selectedLabels": len(delta_labels),
            "selectedSignatures": len(delta),
            "pairs": [pair_text(pair) for pair in sorted_delta],
            "signatureBaseline": "completed pinned v16 boundary, 14/14 census rows, 3 routes, exact two-target gold closure",
        },
        "execution": {
            "heavyWorkerLaunched": False,
            "heavyCommandFrozen": bool(args.finalize_heavy_plan),
            "guardedPlannedCommand": guarded_command,
            "outputAbsentAtPreparation": True,
            "outputAbsentGuardRequired": True,
            "requiresRootHeavyWorkerClearance": True,
            "requiresAcceptedBoundaryConfirmation": not args.finalize_heavy_plan,
            "expectedDeltaPairsAtFinalization": args.expected_delta_pairs,
        },
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    assert_coefficient_free(plan, "provenance plan")
    helper.atomic_json(PLAN, plan)
    audit = {
        "schemaVersion": "v17-light-preflight-audit-v1",
        "sealedAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_safe_for_one_heavy_worker" if args.finalize_heavy_plan else "certified_light_snapshot_awaiting_boundary_confirmation",
        "base": {
            "version": "v16",
            "frozenOwnedPairs": len(v16_pairs),
            "completedCensusRows": EXPECTED_V16_DELTA_LABELS,
            "closedConditionalRoutes": EXPECTED_V16_CONDITIONAL_ROUTES,
            "artifacts": boundary_artifacts,
        },
        "receiptBoundary": receipt_boundary,
        "queuedForV18Excluded": dict(QUEUED_FOR_V18),
        "snapshot": {
            "acceptedScoreablePairs": len(accepted_pairs),
            "deltaPairs": len(delta),
            "deltaLabels": len(delta_labels),
            "groupRows": len(group_rows),
            "targetLabels": len(target_labels),
            "targetRows": len(target_map),
        },
        "checks": {
            "v16BoundaryPinned": True,
            "v16CompletedCensusExact": True,
            "v16GoldRoutesExactClosure": True,
            "verifiedReceiptBoundaryA1115AndB4f7": len(receipt_boundary) == 2,
            "queued6eeAndBc7ExcludedForV18": True,
            "acceptedScoreableDeltaExact": True,
            "provenanceAnchorsComplete": len(anchors_by_pair) == len(delta),
            "targetCacheCompleteAndUnique": True,
            "signatureIsolationExact": helper.source_pairs(census_rows) == delta,
            "baselineCollisionCountZero": not bool(delta & baseline),
            "priorFrozenSignatureCollisionCountZero": not bool(delta & prior_signature_pairs),
            "priorMapChainCompleteThroughV16": len(prior_artifacts) == 20,
            "outputAbsentGuardEnabled": True,
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
            "outputAbsentAtSeal": not OUTPUT.exists(),
            "heavyCommandFrozen": bool(args.finalize_heavy_plan),
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "heavyWorkerLaunched": False,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "writesLimitedToV17Artifacts": True,
        },
    }
    if not all(audit["checks"].values()):
        raise ValueError("one or more v17 preflight checks failed")
    assert_coefficient_free(audit, "preflight audit")
    helper.atomic_json(PREFLIGHT, audit)
    print(json.dumps({
        "status": plan["status"],
        "baseOwnedPairs": len(v16_pairs),
        "snapshotOwnedPairs": len(accepted_pairs),
        "deltaPairs": len(delta),
        "selectedWorkerRows": len(delta_labels),
        "selectedWorkerSignatures": len(delta),
        "heavyCommandFrozen": bool(args.finalize_heavy_plan),
        "heavyWorkerLaunched": False,
        "plan": str(PLAN.relative_to(ROOT)),
        "preflight": str(PREFLIGHT.relative_to(ROOT)),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
