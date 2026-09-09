#!/usr/bin/env python3
"""Prepare a coefficient-free accepted-scoreable post-v13 pair delta.

The default mode is deliberately non-final: it refreshes the light v14
artifacts from one read-only ledger snapshot but does not freeze a heavy worker
command.  After the submission queue has been checked, root can explicitly
finalize the plan with ``--finalize-heavy-plan --expected-delta-pairs N``.

This module never imports Sage/GAP, calls the network, writes the ledger, or
submits.  It also refuses to write anything if the heavy-worker output already
exists.
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


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V13 = ROOT / "data/autopilot_pair_delta_20260722_v13"
V14 = ROOT / "data/autopilot_pair_delta_20260722_v14"
GROUP_INPUT = V14 / "group_input.jsonl"
GROUP_SUMMARY = V14 / "group_input_summary.json"
CENSUS_INPUT = V14 / "census_input.jsonl"
INVENTORY = V14 / "exact_source_inventory.json"
PLAN = V14 / "provenance_plan.json"
PREFLIGHT = V14 / "preflight_audit_summary.json"
OUTPUT = V14 / "missing_pair_all.jsonl"

EXPECTED_TARGET_LABELS = 25_000
EXPECTED_TARGET_ROWS = 165_836
EXPECTED_V13_GROUP_ROWS = 13_330
EXPECTED_V13_OWNED_PAIRS = 21_913
EXPECTED_V13_DELTA_LABELS = 79
EXPECTED_V13_DELTA_PAIRS = 90

V13_PINS = {
    "data/autopilot_pair_delta_20260722_v13/group_input.jsonl":
        "744ece7eff1acdd34638221344771ce42efc758d2a79e9061389b68dc62cf382",
    "data/autopilot_pair_delta_20260722_v13/group_input_summary.json":
        "b519ffaf99c609d160944eb87684ac97b38a899b7330c2b85f93e4e06cc6989a",
    "data/autopilot_pair_delta_20260722_v13/census_input.jsonl":
        "ebd863cd0d4fc201632f1a17b6854eeb892b112e95d55f2c4cc679cbe01223b2",
    "data/autopilot_pair_delta_20260722_v13/missing_pair_all.jsonl":
        "b184144aeca61c934c9343daca6bae5a4eb220e26c53d098cb11bc1cfd243fba",
    "data/autopilot_pair_delta_20260722_v13/exact_source_inventory.json":
        "275316fa2059a843f87d038c63d0a8e8aa4706188e17e399a365e47b0a7e1950",
    "data/autopilot_pair_delta_20260722_v13/provenance_plan.json":
        "8d5e47f01784504650e328bc0a70d03ab6fc9d4725350af11ce6887fb9df9320",
    "data/autopilot_pair_delta_20260722_v13/preflight_audit_summary.json":
        "b12fa8d9c7cb70e91720473ba1d7a997b3bd7fa0bc30f34601c35b600f39ffa9",
    "data/autopilot_pair_delta_20260722_v13/route_triage_certificate.json":
        "8cc2db83092ab1ed611efd597ec11a3ab1c14a95fa31e775ce7b5579bd2b1bed",
    "data/autopilot_pair_delta_20260722_v13/route_triage_summary.json":
        "e57171056da658ba85e8af8e79c508a833e7a3f6199aff3ad5526be261d26cae",
    "data/v13_route_lineage_certificate.json":
        "5eacf27cfb7832630cb4d3cd6316e18ca421c467569a89460d188c3b841b6a9c",
    "data/v13_route_closure_summary.json":
        "cdd2672e732721ca6ef61d230d635de38e2879fc7366b30e3ad58d29f1a17990",
    "data/v13_17513_r16_to_16970_pair.jsonl":
        "24ff8d4b49081c23f609b12398be89deb85f39c0b6993b450c5bb4ddf0e7d489",
    "data/v13_17513_r16_to_16970_stage_certificate.json":
        "5e22c5ae30f7c22341084bb0826b2bbda434c5ce85110f84ba47ea47508877fc",
    "stage_v13_route_17513.py":
        "12039a0a3886c1c2826b4859d1d3bf4045840b51bceae483db37e07d67fd2f35",
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
    "data/autopilot_pair_delta_20260722_v13/missing_pair_all.jsonl",
]

PAIR_RE = re.compile(r"(24T[1-9][0-9]*):(0|2|4|6|8|10|12|14|16|18|20|22|24)\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
COEFFICIENT_LINE_RE = re.compile(r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--finalize-heavy-plan",
        action="store_true",
        help="freeze one guarded heavy-worker command after queue confirmation",
    )
    parser.add_argument(
        "--expected-delta-pairs",
        type=int,
        help="required exact post-v13 pair count when finalizing",
    )
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
    if not path.is_file():
        raise ValueError(f"missing pinned v13 artifact: {relative}")
    actual = helper.sha256_path(path)
    if actual != V13_PINS[relative]:
        raise ValueError(f"pinned v13 artifact changed: {relative} ({actual})")


def validate_v13_boundary() -> tuple[list[dict], set[tuple[str, int]]]:
    for relative in V13_PINS:
        require_pin(relative)

    group_rows = helper.read_jsonl(V13 / "group_input.jsonl")
    census_input_rows = helper.read_jsonl(V13 / "census_input.jsonl")
    completed_rows = helper.read_jsonl(V13 / "missing_pair_all.jsonl")
    summary = helper.read_json(V13 / "group_input_summary.json")
    inventory = helper.read_json(V13 / "exact_source_inventory.json")
    plan = helper.read_json(V13 / "provenance_plan.json")
    preflight = helper.read_json(V13 / "preflight_audit_summary.json")
    closure = helper.read_json(ROOT / "data/v13_route_closure_summary.json")

    owned_pairs = helper.source_pairs(group_rows)
    selected_pairs = helper.source_pairs(census_input_rows)
    completed_pairs = {
        (str(row.get("sourceLabel")), int(signature))
        for row in completed_rows
        for signature in row.get("sourceR") or []
    }
    inventory_pairs = {
        parsed_pair(value) for value in inventory.get("deltaPairs") or []
    }
    if len(group_rows) != EXPECTED_V13_GROUP_ROWS:
        raise ValueError("pinned v13 group-row cardinality mismatch")
    if len(owned_pairs) != EXPECTED_V13_OWNED_PAIRS:
        raise ValueError("pinned v13 owned-pair cardinality mismatch")
    if (
        len(census_input_rows) != EXPECTED_V13_GROUP_ROWS
        or len(completed_rows) != EXPECTED_V13_DELTA_LABELS
        or len(selected_pairs) != EXPECTED_V13_DELTA_PAIRS
        or selected_pairs != completed_pairs
        or selected_pairs != inventory_pairs
    ):
        raise ValueError("v13 completed census does not exactly cover its frozen delta")
    if any(
        row.get("status") != "certified" or not helper.certificate_is_exact(row)
        for row in completed_rows
    ):
        raise ValueError("v13 completed census contains an uncertified row")
    if (
        summary.get("schemaVersion") != "v13-frozen-group-input-summary-v1"
        or summary.get("status") != "certified"
        or int(summary.get("frozenOwnedPairs", -1)) != EXPECTED_V13_OWNED_PAIRS
        or int(summary.get("deltaPairs", -1)) != EXPECTED_V13_DELTA_PAIRS
        or int(summary.get("targetLabels", -1)) != EXPECTED_TARGET_LABELS
        or int(summary.get("targetRows", -1)) != EXPECTED_TARGET_ROWS
    ):
        raise ValueError("v13 group summary envelope mismatch")
    if (
        inventory.get("schemaVersion") != "v13-accepted-scoreable-source-inventory-v1"
        or inventory.get("status") != "certified"
        or int(inventory.get("deltaPairCount", -1)) != EXPECTED_V13_DELTA_PAIRS
        or int(inventory.get("deltaLabelCount", -1)) != EXPECTED_V13_DELTA_LABELS
        or set((inventory.get("acceptedAnchorsByPair") or {}))
        != {pair_text(pair) for pair in selected_pairs}
    ):
        raise ValueError("v13 accepted-source inventory envelope mismatch")
    if (
        plan.get("schemaVersion") != "v13-pair-delta-provenance-plan-v1"
        or plan.get("status") != "ready_for_one_heavy_worker"
        or int((plan.get("delta") or {}).get("selectedSignatures", -1))
        != EXPECTED_V13_DELTA_PAIRS
    ):
        raise ValueError("v13 provenance plan envelope mismatch")
    checks = preflight.get("checks") or {}
    positive_checks = {
        "acceptedScoreableDeltaExact",
        "artifactHashesValid",
        "priorMapChainComplete",
        "provenanceAnchorsComplete",
        "signatureIsolationExact",
        "targetCacheCompleteAndUnique",
        "v12BoundaryPinned",
        "workerCommandExact",
    }
    if (
        preflight.get("schemaVersion") != "v13-light-preflight-audit-v1"
        or preflight.get("status") != "certified_safe_for_one_heavy_worker"
        or preflight.get("coefficientMaterialIncluded") is not False
        or any(checks.get(name) is not True for name in positive_checks)
        or int(checks.get("baselineCollisionCount", -1)) != 0
        or int(checks.get("priorFrozenSignatureCollisionCount", -1)) != 0
    ):
        raise ValueError("v13 light preflight is not fully certified")
    closure_counts = closure.get("counts") or {}
    if (
        closure.get("schemaVersion") != "v13-all-conditional-route-closure-summary-v1"
        or closure.get("status") != "all_four_conditional_routes_closed_exact_misses"
        or int(closure_counts.get("conditionalRoutes", -1)) != 4
        or int(closure_counts.get("exactMisses", -1)) != 4
        or int(closure_counts.get("remainingConditionalRoutes", -1)) != 0
        or int(closure.get("projectedMarginalScoreExact", -1)) != 0
    ):
        raise ValueError("v13 conditional-route closure is incomplete")
    return group_rows, owned_pairs


def validate_prior_maps(
    v13_pairs: set[tuple[str, int]],
) -> tuple[list[dict], set[tuple[str, int]]]:
    if PRIOR_MAPS[-1] != "data/autopilot_pair_delta_20260722_v13/missing_pair_all.jsonl":
        raise ValueError("prior-map chain does not terminate at v13")
    if len(PRIOR_MAPS) != len(set(PRIOR_MAPS)):
        raise ValueError("prior-map chain contains duplicate paths")

    artifacts = []
    signature_pairs: set[tuple[str, int]] = set()
    for relative in PRIOR_MAPS:
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"prior map is absent or empty: {relative}")
        rows = helper.read_jsonl(path)
        for row in rows:
            label = row.get("sourceLabel")
            signatures = row.get("sourceR")
            if label is not None and isinstance(signatures, list):
                signature_pairs.update((str(label), int(r)) for r in signatures)
        artifacts.append(helper.relative_artifact(path))
    if not signature_pairs <= v13_pairs:
        raise ValueError("prior-map signature chain escapes the pinned v13 boundary")
    if artifacts[-1]["sha256"] != V13_PINS[PRIOR_MAPS[-1]]:
        raise ValueError("terminal prior map is not the pinned completed v13 census")
    return artifacts, signature_pairs


def ledger_snapshot() -> dict:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
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
        accepted_anchor_rows = int(connection.execute(
            "SELECT COUNT(*) FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        ).fetchone()[0])
        target_records = [
            (str(label), int(r), int(team_count), bool(discovered), str(stamp))
            for label, r, team_count, discovered, stamp in connection.execute(
                "SELECT label,r,team_count,discovered,generated_at FROM targets"
            )
        ]
        latest_sync = connection.execute(
            "SELECT MAX(synced_at) FROM submissions"
        ).fetchone()[0]
        return {
            "connection": connection,
            "baseline": baseline,
            "acceptedPairs": accepted_pairs,
            "acceptedAnchorRows": accepted_anchor_rows,
            "targetRecords": target_records,
            "latestSubmissionSyncEpoch": latest_sync,
        }
    except BaseException:
        connection.close()
        raise


def accepted_anchors(
    connection: sqlite3.Connection,
    delta: set[tuple[str, int]],
) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for label, r in sorted(delta, key=pair_key):
        rows = list(connection.execute(
            "SELECT v.submission_id,v.polynomial_index,p.coefficient_hash,"
            "v.scoring_status,v.in_baseline,v.baseline_unlocked "
            "FROM verifications v JOIN polynomials p "
            "USING(submission_id,polynomial_index) "
            "WHERE v.label=? AND v.r=? AND v.status='accepted' AND v.scoreable=1 "
            "ORDER BY v.submission_id,v.polynomial_index",
            (label, r),
        ))
        anchors = []
        for submission_id, index, coefficient_hash, scoring_status, in_baseline, unlocked in rows:
            coefficient_hash = str(coefficient_hash)
            if not HASH_RE.fullmatch(coefficient_hash):
                raise ValueError(f"accepted anchor for {label}:{r} has an invalid hash")
            if str(scoring_status) != "scoreable" or bool(in_baseline):
                raise ValueError(f"accepted anchor for {label}:{r} is not exactly scoreable")
            anchors.append({
                "submissionId": str(submission_id),
                "polynomialIndex": int(index),
                "coefficientSha256": coefficient_hash,
                "scoringStatus": str(scoring_status),
                "inBaseline": bool(in_baseline),
                "baselineUnlocked": bool(unlocked),
            })
        if not anchors:
            raise ValueError(f"post-v13 pair {label}:{r} has no exact accepted anchor")
        result[pair_text((label, r))] = anchors
    return result


def assert_coefficient_free(value: object, name: str) -> None:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if COEFFICIENT_LINE_RE.search(encoded):
        raise ValueError(f"{name} contains a degree-24 coefficient line")
    lowered = encoded.lower()
    if '"coefficients"' in lowered or '"originalline"' in lowered:
        raise ValueError(f"{name} contains raw coefficient material")
    if "bearer " in lowered or '"authorization"' in lowered:
        raise ValueError(f"{name} contains credential material")


def main() -> int:
    args = parse_args()
    if args.finalize_heavy_plan and args.expected_delta_pairs is None:
        raise ValueError("--expected-delta-pairs is required when finalizing")
    if args.expected_delta_pairs is not None and args.expected_delta_pairs <= 0:
        raise ValueError("--expected-delta-pairs must be positive")
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite heavy-worker output: {OUTPUT}")

    _, v13_pairs = validate_v13_boundary()
    prior_artifacts, prior_signature_pairs = validate_prior_maps(v13_pairs)
    snapshot = ledger_snapshot()
    connection = snapshot.pop("connection")
    try:
        baseline = snapshot["baseline"]
        accepted_pairs = snapshot["acceptedPairs"]
        if not v13_pairs <= accepted_pairs:
            raise ValueError("accepted ledger regressed behind the pinned v13 boundary")
        delta = accepted_pairs - v13_pairs
        if not delta:
            raise ValueError("post-v13 accepted-scoreable delta is empty")
        if delta & baseline:
            raise ValueError("post-v13 accepted delta intersects the baseline")
        if delta & prior_signature_pairs:
            raise ValueError("post-v13 accepted delta collides with a prior-map signature")
        if (
            args.expected_delta_pairs is not None
            and len(delta) != args.expected_delta_pairs
        ):
            raise ValueError(
                f"expected {args.expected_delta_pairs} post-v13 pairs, found {len(delta)}"
            )
        anchors_by_pair = accepted_anchors(connection, delta)
    finally:
        connection.close()

    target_records = snapshot["targetRecords"]
    target_map: dict[tuple[str, int], tuple[int, bool, str]] = {}
    target_labels: set[str] = set()
    for label, r, team_count, discovered, stamp in target_records:
        if not re.fullmatch(r"24T(?:[1-9][0-9]*)", label):
            raise ValueError(f"invalid cached target label: {label!r}")
        if r not in range(0, 25, 2):
            raise ValueError(f"invalid cached target signature: {label}:{r}")
        pair = (label, r)
        if pair in target_map:
            raise ValueError(f"duplicate cached target pair: {pair_text(pair)}")
        target_map[pair] = (team_count, discovered, stamp)
        target_labels.add(label)
    expected_labels = {f"24T{index}" for index in range(1, EXPECTED_TARGET_LABELS + 1)}
    if (
        len(target_records) != EXPECTED_TARGET_ROWS
        or len(target_map) != EXPECTED_TARGET_ROWS
        or target_labels != expected_labels
    ):
        raise ValueError(
            f"target cache incomplete: {len(target_labels)} labels/"
            f"{len(target_map)} unique pairs/{len(target_records)} rows"
        )
    if not delta <= set(target_map):
        raise ValueError("one or more post-v13 pairs are absent from the target cache")

    owned_by_label: dict[str, set[int]] = {}
    for label, r in accepted_pairs:
        owned_by_label.setdefault(label, set()).add(r)
    gold_by_label: dict[str, set[int]] = {}
    for pair, (team_count, _, _) in target_map.items():
        label, r = pair
        if team_count == 0 and pair not in baseline and pair not in accepted_pairs:
            gold_by_label.setdefault(label, set()).add(r)

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
        raise ValueError("v14 group input does not reproduce accepted-scoreable pairs")

    census_rows = []
    delta_labels = {label for label, _ in delta}
    for row in group_rows:
        selected = sorted(r for label, r in delta if label == row["label"])
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if helper.source_pairs(census_rows) != delta:
        raise ValueError("v14 census input does not exactly isolate the post-v13 delta")

    sorted_delta = sorted(delta, key=pair_key)
    delta_snapshot = [
        {
            "pair": pair_text(pair),
            "teamCount": target_map[pair][0],
            "discovered": target_map[pair][1],
            "generatedAt": target_map[pair][2],
        }
        for pair in sorted_delta
    ]
    boundary_artifacts = {
        "groupInput": helper.relative_artifact(V13 / "group_input.jsonl"),
        "completedCensus": helper.relative_artifact(V13 / "missing_pair_all.jsonl"),
        "routeClosure": helper.relative_artifact(ROOT / "data/v13_route_closure_summary.json"),
    }
    group_summary = {
        "schemaVersion": "v14-refreshable-group-input-summary-v1",
        "status": (
            "certified_boundary_confirmed" if args.finalize_heavy_plan
            else "prepared_awaiting_boundary_confirmation"
        ),
        "base": boundary_artifacts,
        "groupInput": {"path": str(GROUP_INPUT.relative_to(ROOT))},
        "rows": len(group_rows),
        "baseOwnedPairs": len(v13_pairs),
        "snapshotOwnedPairs": len(accepted_pairs),
        "deltaPairs": len(delta),
        "deltaLabels": len(delta_labels),
        "targetLabels": len(target_labels),
        "targetRows": len(target_map),
        "targetGeneratedAtMin": min(stamp for *_, stamp in target_records),
        "targetGeneratedAtMax": max(stamp for *_, stamp in target_records),
        "heavyPlanFinalized": bool(args.finalize_heavy_plan),
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    inventory = {
        "schemaVersion": "v14-accepted-scoreable-source-inventory-v1",
        "status": "certified",
        "base": boundary_artifacts,
        "ledgerSnapshot": {
            "acceptedScoreablePairs": len(accepted_pairs),
            "acceptedScoreableAnchorRows": snapshot["acceptedAnchorRows"],
            "latestSubmissionSyncEpoch": snapshot["latestSubmissionSyncEpoch"],
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

    group_text = helper.canonical_jsonl(group_rows)
    census_text = helper.canonical_jsonl(census_rows)
    assert_coefficient_free(group_rows, "group input")
    assert_coefficient_free(census_rows, "census input")
    assert_coefficient_free(group_summary, "group summary")
    assert_coefficient_free(inventory, "source inventory")
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
    for prior in PRIOR_MAPS:
        worker_argv.extend(["--prior-map", prior])
    worker_argv.extend([
        "--signature-aware",
        "--prior-input", "data/autopilot_pair_delta_20260722_v13/group_input.jsonl",
        "--output", str(OUTPUT.relative_to(ROOT)),
        "--shard-index", "0", "--shard-count", "1",
        "--checkpoint-every", "1",
    ])
    guarded_command = None
    if args.finalize_heavy_plan:
        output_relative = str(OUTPUT.relative_to(ROOT))
        guard = (
            f"test ! -e {shlex.quote(output_relative)} && "
            f"exec {shlex.join(worker_argv)}"
        )
        guarded_command = ["/bin/sh", "-c", guard]

    plan = {
        "schemaVersion": "v14-pair-delta-provenance-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "ready_for_one_heavy_worker" if args.finalize_heavy_plan
            else "prepared_awaiting_boundary_confirmation"
        ),
        "artifacts": {
            "groupInput": helper.relative_artifact(GROUP_INPUT),
            "groupInputSummary": helper.relative_artifact(GROUP_SUMMARY),
            "censusInput": helper.relative_artifact(CENSUS_INPUT),
            "exactSourceInventory": helper.relative_artifact(INVENTORY),
            "preparer": helper.relative_artifact(Path(__file__).resolve()),
            "priorMaps": prior_artifacts,
            "worker": helper.relative_artifact(
                ROOT / "agent_index24_missing_pair_census.sage.py"
            ),
            "base": boundary_artifacts,
        },
        "delta": {
            "selectedLabels": len(delta_labels),
            "selectedSignatures": len(delta),
            "pairs": [pair_text(pair) for pair in sorted_delta],
            "signatureBaseline": (
                "completed pinned v13 boundary, 79/79 census, and 4/4 exact route closure"
            ),
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
        "schemaVersion": "v14-light-preflight-audit-v1",
        "sealedAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_safe_for_one_heavy_worker" if args.finalize_heavy_plan
            else "certified_light_snapshot_awaiting_boundary_confirmation"
        ),
        "base": {
            "version": "v13",
            "frozenOwnedPairs": len(v13_pairs),
            "completedCensusRows": EXPECTED_V13_DELTA_LABELS,
            "closedConditionalRoutes": 4,
            "artifacts": boundary_artifacts,
        },
        "snapshot": {
            "acceptedScoreablePairs": len(accepted_pairs),
            "deltaPairs": len(delta),
            "deltaLabels": len(delta_labels),
            "groupRows": len(group_rows),
            "targetLabels": len(target_labels),
            "targetRows": len(target_map),
        },
        "checks": {
            "v13BoundaryPinned": True,
            "v13CompletedCensusExact": True,
            "v13RouteClosureComplete": True,
            "acceptedScoreableDeltaExact": True,
            "provenanceAnchorsComplete": len(anchors_by_pair) == len(delta),
            "targetCacheCompleteAndUnique": True,
            "signatureIsolationExact": helper.source_pairs(census_rows) == delta,
            "baselineCollisionCountZero": not bool(delta & baseline),
            "priorFrozenSignatureCollisionCountZero": not bool(delta & prior_signature_pairs),
            "priorMapChainCompleteThroughV13": len(prior_artifacts) == len(PRIOR_MAPS),
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
            "writesLimitedToV14Artifacts": True,
        },
    }
    if not all(audit["checks"].values()):
        raise ValueError("one or more v14 light preflight checks failed")
    assert_coefficient_free(audit, "preflight audit")
    helper.atomic_json(PREFLIGHT, audit)

    print(json.dumps({
        "status": plan["status"],
        "baseOwnedPairs": len(v13_pairs),
        "snapshotOwnedPairs": len(accepted_pairs),
        "deltaPairs": len(delta),
        "selectedWorkerRows": len(delta_labels),
        "selectedWorkerSignatures": len(delta),
        "heavyCommandFrozen": bool(args.finalize_heavy_plan),
        "outputAbsentGuardEnabled": True,
        "plan": str(PLAN.relative_to(ROOT)),
        "preflight": str(PREFLIGHT.relative_to(ROOT)),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
