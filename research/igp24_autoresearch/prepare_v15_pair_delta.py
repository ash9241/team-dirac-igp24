#!/usr/bin/env python3
"""Prepare a coefficient-free accepted-scoreable post-v14 pair delta.

This is a two-phase, light-only preparer.  The default phase refreshes the
v15 ledger snapshot and provenance artifacts without freezing or launching a
heavy command.  Once the submission queue is drained, root may explicitly
freeze the guarded command with ``--finalize-heavy-plan`` and an exact expected
delta cardinality.  Even finalization does not execute Sage/GAP.

The module performs no network calls, submissions, or ledger writes.  It also
refuses to write if the heavy-worker output already exists.
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
V14 = ROOT / "data/autopilot_pair_delta_20260722_v14"
V15 = ROOT / "data/autopilot_pair_delta_20260722_v15"
GROUP_INPUT = V15 / "group_input.jsonl"
GROUP_SUMMARY = V15 / "group_input_summary.json"
CENSUS_INPUT = V15 / "census_input.jsonl"
INVENTORY = V15 / "exact_source_inventory.json"
PLAN = V15 / "provenance_plan.json"
PREFLIGHT = V15 / "preflight_audit_summary.json"
OUTPUT = V15 / "missing_pair_all.jsonl"

EXPECTED_TARGET_LABELS = 25_000
EXPECTED_TARGET_ROWS = 165_836
EXPECTED_V14_GROUP_ROWS = 13_329
EXPECTED_V14_OWNED_PAIRS = 21_927
EXPECTED_V14_DELTA_LABELS = 13
EXPECTED_V14_DELTA_PAIRS = 14
EXPECTED_V14_CONDITIONAL_ROUTES = 2

V14_PINS = {
    "prepare_v14_pair_delta.py":
        "1ee0e07c43ae8a490e3833336f13777c1e8131f2cab820a09855eea221f6bf33",
    "audit_v14_route_lineage.py":
        "a2fa338ed907bf4a861b8f10b9508619e620ec58f986d9ff10316fdb48eb3fe4",
    "data/autopilot_pair_delta_20260722_v14/group_input.jsonl":
        "2fee7e4d52ffdd115aa614d55c77e9efcd322ee4e00bed5ef6f381ea52241e3d",
    "data/autopilot_pair_delta_20260722_v14/group_input_summary.json":
        "611a1500f46c4ccf51ce4ca5d7becb2eb9114d1e74de7730175eda7343df6e72",
    "data/autopilot_pair_delta_20260722_v14/census_input.jsonl":
        "e15dc40238806cc584e505a23946d325190d662a3618c0bbdf02ed4c00e0647d",
    "data/autopilot_pair_delta_20260722_v14/missing_pair_all.jsonl":
        "ff00682d803078dad27f5bf8801e06b484515e88b7c8e148a8f2e93c24e5044b",
    "data/autopilot_pair_delta_20260722_v14/exact_source_inventory.json":
        "21e266fa026b0ddf52d426503865ca7470c60a760aa655694796039193c1eba7",
    "data/autopilot_pair_delta_20260722_v14/provenance_plan.json":
        "4aea1783431130cade3067dabb1d2cc7c6824914d03cf6612f2f23dd54e15344",
    "data/autopilot_pair_delta_20260722_v14/preflight_audit_summary.json":
        "c35808e848ea819d135c3fe39083e82826e5dc329b7ed2b2186549d086e92507",
    "data/autopilot_pair_delta_20260722_v14/route_triage_certificate.json":
        "e07702ec3d9fc4acccd219b2211aa7ebb867c2ebfdff8f85bafa6762390855d7",
    "data/autopilot_pair_delta_20260722_v14/route_triage_summary.json":
        "f24595fca1c17c43a30146a3bc713f431c570227aa3e4fea1674c71a8cd6fc36",
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
    "data/autopilot_pair_delta_20260722_v14/missing_pair_all.jsonl",
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
        help="required exact post-v14 pair count when finalizing",
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
        raise ValueError(f"missing pinned v14 artifact: {relative}")
    actual = helper.sha256_path(path)
    if actual != V14_PINS[relative]:
        raise ValueError(f"pinned v14 artifact changed: {relative} ({actual})")


def validate_v14_boundary() -> tuple[list[dict], set[tuple[str, int]]]:
    """Validate the frozen v14 census and its exact 2/2 lineage closure."""
    for relative in V14_PINS:
        require_pin(relative)

    group_rows = helper.read_jsonl(V14 / "group_input.jsonl")
    census_input_rows = helper.read_jsonl(V14 / "census_input.jsonl")
    completed_rows = helper.read_jsonl(V14 / "missing_pair_all.jsonl")
    summary = helper.read_json(V14 / "group_input_summary.json")
    inventory = helper.read_json(V14 / "exact_source_inventory.json")
    plan = helper.read_json(V14 / "provenance_plan.json")
    preflight = helper.read_json(V14 / "preflight_audit_summary.json")
    route_certificate = helper.read_json(V14 / "route_triage_certificate.json")
    route_summary = helper.read_json(V14 / "route_triage_summary.json")

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
    if len(group_rows) != EXPECTED_V14_GROUP_ROWS:
        raise ValueError("pinned v14 group-row cardinality mismatch")
    if len(owned_pairs) != EXPECTED_V14_OWNED_PAIRS:
        raise ValueError("pinned v14 owned-pair cardinality mismatch")
    if (
        len(census_input_rows) != EXPECTED_V14_GROUP_ROWS
        or len(completed_rows) != EXPECTED_V14_DELTA_LABELS
        or len(selected_pairs) != EXPECTED_V14_DELTA_PAIRS
        or selected_pairs != completed_pairs
        or selected_pairs != inventory_pairs
    ):
        raise ValueError("v14 completed census does not exactly cover its frozen delta")
    if any(
        row.get("status") != "certified" or not helper.certificate_is_exact(row)
        for row in completed_rows
    ):
        raise ValueError("v14 completed census contains an uncertified row")
    if (
        summary.get("schemaVersion") != "v14-refreshable-group-input-summary-v1"
        or summary.get("status") != "certified_boundary_confirmed"
        or summary.get("heavyPlanFinalized") is not True
        or int(summary.get("baseOwnedPairs", -1)) != 21_913
        or int(summary.get("snapshotOwnedPairs", -1)) != EXPECTED_V14_OWNED_PAIRS
        or int(summary.get("deltaPairs", -1)) != EXPECTED_V14_DELTA_PAIRS
        or int(summary.get("deltaLabels", -1)) != EXPECTED_V14_DELTA_LABELS
        or int(summary.get("targetLabels", -1)) != EXPECTED_TARGET_LABELS
        or int(summary.get("targetRows", -1)) != EXPECTED_TARGET_ROWS
    ):
        raise ValueError("v14 group summary envelope mismatch")
    if (
        inventory.get("schemaVersion") != "v14-accepted-scoreable-source-inventory-v1"
        or inventory.get("status") != "certified"
        or int(inventory.get("deltaPairCount", -1)) != EXPECTED_V14_DELTA_PAIRS
        or int(inventory.get("deltaLabelCount", -1)) != EXPECTED_V14_DELTA_LABELS
        or set((inventory.get("acceptedAnchorsByPair") or {}))
        != {pair_text(pair) for pair in selected_pairs}
    ):
        raise ValueError("v14 accepted-source inventory envelope mismatch")
    if (
        plan.get("schemaVersion") != "v14-pair-delta-provenance-plan-v1"
        or plan.get("status") != "ready_for_one_heavy_worker"
        or (plan.get("execution") or {}).get("heavyCommandFrozen") is not True
        or int((plan.get("delta") or {}).get("selectedSignatures", -1))
        != EXPECTED_V14_DELTA_PAIRS
    ):
        raise ValueError("v14 provenance plan envelope mismatch")
    checks = preflight.get("checks") or {}
    positive_checks = {
        "acceptedScoreableDeltaExact",
        "baselineCollisionCountZero",
        "coefficientAndCredentialScanClean",
        "outputAbsentGuardEnabled",
        "priorFrozenSignatureCollisionCountZero",
        "priorMapChainCompleteThroughV13",
        "provenanceAnchorsComplete",
        "signatureIsolationExact",
        "targetCacheCompleteAndUnique",
        "v13BoundaryPinned",
        "v13CompletedCensusExact",
        "v13RouteClosureComplete",
    }
    if (
        preflight.get("schemaVersion") != "v14-light-preflight-audit-v1"
        or preflight.get("status") != "certified_safe_for_one_heavy_worker"
        or preflight.get("coefficientMaterialIncluded") is not False
        or preflight.get("credentialMaterialIncluded") is not False
        or any(checks.get(name) is not True for name in positive_checks)
    ):
        raise ValueError("v14 light preflight is not fully certified")

    route_counts = route_certificate.get("counts") or {}
    summary_census = route_summary.get("census") or {}
    summary_triage = route_summary.get("routeTriage") or {}
    if (
        route_certificate.get("schemaVersion")
        != "v14-coefficient-free-route-triage-certificate-v1"
        or route_certificate.get("status")
        != "all_conditional_routes_closed_exact_lineage_misses"
        or route_certificate.get("coefficientMaterialIncluded") is not False
        or route_certificate.get("credentialMaterialIncluded") is not False
        or int(route_counts.get("conditionalRoutes", -1))
        != EXPECTED_V14_CONDITIONAL_ROUTES
        or int(route_counts.get("cachedExactLineageClosures", -1))
        != EXPECTED_V14_CONDITIONAL_ROUTES
        or int(route_counts.get("exactMisses", -1))
        != EXPECTED_V14_CONDITIONAL_ROUTES
        or int(route_counts.get("unresolvedExecutableRoutes", -1)) != 0
        or len(route_certificate.get("routes") or [])
        != EXPECTED_V14_CONDITIONAL_ROUTES
    ):
        raise ValueError("v14 route-lineage certificate is not an exact 2/2 closure")
    if (
        route_summary.get("schemaVersion")
        != "v14-coefficient-free-route-triage-summary-v1"
        or route_summary.get("status")
        != "all_conditional_routes_closed_exact_lineage_misses"
        or int(summary_census.get("conditionalRoutes", -1))
        != EXPECTED_V14_CONDITIONAL_ROUTES
        or int(summary_triage.get("cachedExactLineageClosures", -1))
        != EXPECTED_V14_CONDITIONAL_ROUTES
        or int(summary_triage.get("exactSignatureMisses", -1))
        != EXPECTED_V14_CONDITIONAL_ROUTES
        or int(summary_triage.get("unresolvedExecutableRoutes", -1)) != 0
        or summary_triage.get("prioritizedExecutableRoutes") != []
    ):
        raise ValueError("v14 route-triage summary is not an exact 2/2 closure")
    return group_rows, owned_pairs


def validate_prior_maps(
    v14_pairs: set[tuple[str, int]],
) -> tuple[list[dict], set[tuple[str, int]]]:
    terminal = "data/autopilot_pair_delta_20260722_v14/missing_pair_all.jsonl"
    if PRIOR_MAPS[-1] != terminal:
        raise ValueError("prior-map chain does not terminate at v14")
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
    if not signature_pairs <= v14_pairs:
        raise ValueError("prior-map signature chain escapes the pinned v14 boundary")
    if artifacts[-1]["sha256"] != V14_PINS[terminal]:
        raise ValueError("terminal prior map is not the pinned completed v14 census")
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
            raise ValueError(f"post-v14 pair {label}:{r} has no exact accepted anchor")
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

    _, v14_pairs = validate_v14_boundary()
    prior_artifacts, prior_signature_pairs = validate_prior_maps(v14_pairs)
    snapshot = ledger_snapshot()
    connection = snapshot.pop("connection")
    try:
        baseline = snapshot["baseline"]
        accepted_pairs = snapshot["acceptedPairs"]
        if not v14_pairs <= accepted_pairs:
            raise ValueError("accepted ledger regressed behind the pinned v14 boundary")
        delta = accepted_pairs - v14_pairs
        if not delta:
            raise ValueError("post-v14 accepted-scoreable delta is empty")
        if delta & baseline:
            raise ValueError("post-v14 accepted delta intersects the baseline")
        if delta & prior_signature_pairs:
            raise ValueError("post-v14 accepted delta collides with a prior-map signature")
        if (
            args.expected_delta_pairs is not None
            and len(delta) != args.expected_delta_pairs
        ):
            raise ValueError(
                f"expected {args.expected_delta_pairs} post-v14 pairs, found {len(delta)}"
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
        raise ValueError("one or more post-v14 pairs are absent from the target cache")

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
        raise ValueError("v15 group input does not reproduce accepted-scoreable pairs")

    census_rows = []
    delta_labels = {label for label, _ in delta}
    for row in group_rows:
        selected = sorted(r for label, r in delta if label == row["label"])
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if helper.source_pairs(census_rows) != delta:
        raise ValueError("v15 census input does not exactly isolate the post-v14 delta")

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
        "groupInput": helper.relative_artifact(V14 / "group_input.jsonl"),
        "completedCensus": helper.relative_artifact(V14 / "missing_pair_all.jsonl"),
        "preflight": helper.relative_artifact(V14 / "preflight_audit_summary.json"),
        "routeTriageCertificate": helper.relative_artifact(
            V14 / "route_triage_certificate.json"
        ),
        "routeTriageSummary": helper.relative_artifact(V14 / "route_triage_summary.json"),
    }
    group_summary = {
        "schemaVersion": "v15-refreshable-group-input-summary-v1",
        "status": (
            "certified_boundary_confirmed" if args.finalize_heavy_plan
            else "prepared_awaiting_boundary_confirmation"
        ),
        "base": boundary_artifacts,
        "groupInput": {"path": str(GROUP_INPUT.relative_to(ROOT))},
        "rows": len(group_rows),
        "baseOwnedPairs": len(v14_pairs),
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
        "schemaVersion": "v15-accepted-scoreable-source-inventory-v1",
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
        "--prior-input", "data/autopilot_pair_delta_20260722_v14/group_input.jsonl",
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
        "schemaVersion": "v15-pair-delta-provenance-plan-v1",
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
                "completed pinned v14 boundary, 13/13 census rows, "
                "and 2/2 exact lineage closure"
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
        "schemaVersion": "v15-light-preflight-audit-v1",
        "sealedAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_safe_for_one_heavy_worker" if args.finalize_heavy_plan
            else "certified_light_snapshot_awaiting_boundary_confirmation"
        ),
        "base": {
            "version": "v14",
            "frozenOwnedPairs": len(v14_pairs),
            "completedCensusRows": EXPECTED_V14_DELTA_LABELS,
            "closedConditionalRoutes": EXPECTED_V14_CONDITIONAL_ROUTES,
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
            "v14BoundaryPinned": True,
            "v14CompletedCensusExact": True,
            "v14LineageClosureTwoOfTwo": True,
            "acceptedScoreableDeltaExact": True,
            "provenanceAnchorsComplete": len(anchors_by_pair) == len(delta),
            "targetCacheCompleteAndUnique": True,
            "signatureIsolationExact": helper.source_pairs(census_rows) == delta,
            "baselineCollisionCountZero": not bool(delta & baseline),
            "priorFrozenSignatureCollisionCountZero": not bool(delta & prior_signature_pairs),
            "priorMapChainCompleteThroughV14": len(prior_artifacts) == len(PRIOR_MAPS),
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
            "writesLimitedToV15Artifacts": True,
        },
    }
    if not all(audit["checks"].values()):
        raise ValueError("one or more v15 light preflight checks failed")
    assert_coefficient_free(audit, "preflight audit")
    helper.atomic_json(PREFLIGHT, audit)

    print(json.dumps({
        "status": plan["status"],
        "baseOwnedPairs": len(v14_pairs),
        "snapshotOwnedPairs": len(accepted_pairs),
        "deltaPairs": len(delta),
        "selectedWorkerRows": len(delta_labels),
        "selectedWorkerSignatures": len(delta),
        "heavyCommandFrozen": bool(args.finalize_heavy_plan),
        "heavyWorkerLaunched": False,
        "outputAbsentGuardEnabled": True,
        "plan": str(PLAN.relative_to(ROOT)),
        "preflight": str(PREFLIGHT.relative_to(ROOT)),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
