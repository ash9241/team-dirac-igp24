#!/usr/bin/env python3
"""Freeze the accepted-scoreable post-v12 unordered-pair signature delta.

This preparer is deliberately light and offline.  It reads the immutable v12
boundary, the local accepted ledger, and the most recent complete target cache;
it never imports Sage/GAP, calls the network, writes the ledger, or submits.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import prepare_v11_pair_delta as helper


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V12 = ROOT / "data/autopilot_pair_delta_20260722_v12"
V13 = ROOT / "data/autopilot_pair_delta_20260722_v13"
GROUP_INPUT = V13 / "group_input.jsonl"
GROUP_SUMMARY = V13 / "group_input_summary.json"
CENSUS_INPUT = V13 / "census_input.jsonl"
INVENTORY = V13 / "exact_source_inventory.json"
PLAN = V13 / "provenance_plan.json"
OUTPUT = V13 / "missing_pair_all.jsonl"

V12_PINS = {
    "group_input.jsonl": "dcf352f9c5f1f92a4bc140389a612d384404c452ecbde572d7d66502397208e2",
    "census_input.jsonl": "ff328a5d136be0ce8f6473d757fa791636fabf14c36613f1165ed13c04c92875",
    "missing_pair_all.jsonl": "77fc0a0ed7ad213308f028197af5b7a4137783c71ca8a2107162bdf2df26ef66",
    "exact_source_inventory.json": "4998eeb030eff83547befa6074ee12873a7477a2810adfab8766e7915f9739ea",
    "provenance_plan.json": "fa578c7cfb8c9ec9719b0b1ef37ddd56486022e52aed444626216307fac1c04f",
}

PRIOR_MAPS = [
    "data/autopilot_pair_delta_20260721_v2/pair_prior_sources.jsonl",
    *[
        f"data/autopilot_pair_delta_20260721_v2/missing_pair_shard{index}.jsonl"
        for index in range(6)
    ],
    "data/autopilot_pair_delta_20260722_v3/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v4/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v5/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v6/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v8/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v9/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v10/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v11/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v12/missing_pair_all.jsonl",
]


def pair_key(pair: tuple[str, int]) -> tuple[int, int]:
    return int(pair[0][3:]), int(pair[1])


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}:{pair[1]}"


def validate_v12() -> tuple[list[dict], set[tuple[str, int]]]:
    for name, expected in V12_PINS.items():
        path = V12 / name
        actual = helper.sha256_path(path)
        if actual != expected:
            raise ValueError(f"v12 {name} hash mismatch: {actual}")
    rows = helper.read_jsonl(V12 / "group_input.jsonl")
    pairs = helper.source_pairs(rows)
    if len(rows) != 13_328 or len(pairs) != 21_823:
        raise ValueError("v12 group boundary cardinality mismatch")
    census = helper.read_jsonl(V12 / "missing_pair_all.jsonl")
    if len(census) != 45 or any(row.get("status") != "certified" for row in census):
        raise ValueError("v12 census is not the completed 45/45 certificate")
    return rows, pairs


def main() -> int:
    v12_rows, v12_pairs = validate_v12()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    accepted_records = [
        {
            "submissionId": str(submission_id),
            "polynomialIndex": int(polynomial_index),
            "label": str(label),
            "r": int(r),
            "coefficientSha256": str(coefficient_hash),
        }
        for submission_id, polynomial_index, label, r, coefficient_hash
        in connection.execute(
            "SELECT v.submission_id,v.polynomial_index,v.label,v.r,p.coefficient_hash "
            "FROM verifications v JOIN polynomials p "
            "USING(submission_id,polynomial_index) "
            "WHERE v.status='accepted' AND v.scoreable=1"
        )
    ]
    accepted_pairs = {(row["label"], row["r"]) for row in accepted_records}
    if not v12_pairs <= accepted_pairs:
        raise ValueError("accepted ledger regressed behind the v12 boundary")
    delta = accepted_pairs - v12_pairs
    if not delta:
        raise ValueError("post-v12 accepted-scoreable delta is empty")
    if delta & baseline:
        raise ValueError("post-v12 accepted delta intersects the baseline")

    target_records = [
        (str(label), int(r), int(team_count), bool(discovered), str(generated_at))
        for label, r, team_count, discovered, generated_at in connection.execute(
            "SELECT label,r,team_count,discovered,generated_at FROM targets"
        )
    ]
    connection.close()
    target_labels = {label for label, *_ in target_records}
    if len(target_records) != 165_836 or len(target_labels) != 25_000:
        raise ValueError(
            f"target cache is incomplete: {len(target_labels)} labels/{len(target_records)} rows"
        )

    owned_by_label: dict[str, set[int]] = {}
    for label, r in accepted_pairs:
        owned_by_label.setdefault(label, set()).add(r)
    gold_by_label: dict[str, set[int]] = {}
    generated_at = []
    delta_snapshot = {}
    for label, r, team_count, discovered, stamp in target_records:
        generated_at.append(stamp)
        pair = (label, r)
        if pair in delta:
            delta_snapshot[pair] = {
                "pair": pair_text(pair),
                "teamCount": team_count,
                "discovered": discovered,
                "generatedAt": stamp,
            }
        if team_count == 0 and pair not in baseline and pair not in accepted_pairs:
            gold_by_label.setdefault(label, set()).add(r)
    if set(delta_snapshot) != delta:
        raise ValueError("one or more post-v12 pairs are absent from the target cache")

    labels = sorted(set(owned_by_label) | set(gold_by_label), key=lambda x: int(x[3:]))
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
        raise ValueError("v13 group input does not reproduce accepted-scoreable pairs")
    helper.atomic_text(GROUP_INPUT, helper.canonical_jsonl(group_rows))

    census_rows = []
    for row in group_rows:
        selected = sorted(r for label, r in delta if label == row["label"])
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if helper.source_pairs(census_rows) != delta:
        raise ValueError("v13 census input does not isolate the post-v12 delta")
    selected_labels = sum(bool(row["isOwnedSource"]) for row in census_rows)
    helper.atomic_text(CENSUS_INPUT, helper.canonical_jsonl(census_rows))

    delta_records = [
        row for row in accepted_records if (row["label"], row["r"]) in delta
    ]
    anchors_by_pair: dict[str, list[dict]] = {}
    for row in delta_records:
        key = pair_text((row["label"], row["r"]))
        anchors_by_pair.setdefault(key, []).append(row)
    if set(anchors_by_pair) != {pair_text(pair) for pair in delta}:
        raise ValueError("a delta pair lacks an accepted polynomial anchor")

    summary = {
        "schemaVersion": "v13-frozen-group-input-summary-v1",
        "status": "certified",
        "base": helper.relative_artifact(V12 / "group_input.jsonl"),
        "groupInput": helper.relative_artifact(GROUP_INPUT),
        "rows": len(group_rows),
        "baseOwnedPairs": len(v12_pairs),
        "frozenOwnedPairs": len(accepted_pairs),
        "deltaPairs": len(delta),
        "deltaLabels": selected_labels,
        "targetLabels": len(target_labels),
        "targetRows": len(target_records),
        "targetGeneratedAtMin": min(generated_at),
        "targetGeneratedAtMax": max(generated_at),
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    helper.atomic_json(GROUP_SUMMARY, summary)

    inventory = {
        "schemaVersion": "v13-accepted-scoreable-source-inventory-v1",
        "status": "certified",
        "base": {
            "groupInput": helper.relative_artifact(V12 / "group_input.jsonl"),
            "completedCensus": helper.relative_artifact(V12 / "missing_pair_all.jsonl"),
        },
        "deltaPairCount": len(delta),
        "deltaLabelCount": selected_labels,
        "deltaPairs": [pair_text(pair) for pair in sorted(delta, key=pair_key)],
        "acceptedAnchorsByPair": anchors_by_pair,
        "targetSnapshot": [delta_snapshot[pair] for pair in sorted(delta, key=pair_key)],
        "collisionCensus": {
            "baselinePairCollisions": 0,
            "priorFrozenSignatureCollisions": 0,
            "unanchoredDeltaPairs": 0,
        },
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    helper.atomic_json(INVENTORY, inventory)

    command = [
        "caffeinate", "-i", "sage", "-python",
        "agent_index24_missing_pair_census.sage.py",
        "--input", str(CENSUS_INPUT.relative_to(ROOT)),
    ]
    for prior in PRIOR_MAPS:
        command.extend(["--prior-map", prior])
    command.extend([
        "--signature-aware",
        "--prior-input", "data/autopilot_pair_delta_20260722_v12/group_input.jsonl",
        "--output", str(OUTPUT.relative_to(ROOT)),
        "--shard-index", "0", "--shard-count", "1", "--checkpoint-every", "1",
    ])
    plan = {
        "schemaVersion": "v13-pair-delta-provenance-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_one_heavy_worker",
        "artifacts": {
            "groupInput": helper.relative_artifact(GROUP_INPUT),
            "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY),
            "censusInput": helper.relative_artifact(CENSUS_INPUT),
            "exactSourceInventory": helper.relative_artifact(INVENTORY),
            "preparer": helper.relative_artifact(Path(__file__).resolve()),
            "priorMaps": [helper.relative_artifact(ROOT / path) for path in PRIOR_MAPS],
            "worker": helper.relative_artifact(ROOT / "agent_index24_missing_pair_census.sage.py"),
        },
        "delta": {
            "selectedLabels": selected_labels,
            "selectedSignatures": len(delta),
            "pairs": [pair_text(pair) for pair in sorted(delta, key=pair_key)],
            "signatureBaseline": "completed pinned v12 boundary and 45/45 census",
        },
        "execution": {
            "heavyWorkerLaunched": False,
            "plannedCommand": command,
            "requiresRootHeavyWorkerClearance": True,
        },
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    helper.atomic_json(PLAN, plan)
    print(json.dumps({
        "status": "ready_for_one_heavy_worker",
        "deltaPairs": len(delta),
        "selectedWorkerRows": selected_labels,
        "selectedWorkerSignatures": len(delta),
        "plannedCommand": command,
        "plan": str(PLAN.relative_to(ROOT)),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
