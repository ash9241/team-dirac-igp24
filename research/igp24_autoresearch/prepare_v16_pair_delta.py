#!/usr/bin/env python3
"""Prepare a coefficient-free accepted-scoreable post-v15 pair delta.

This is a two-phase, light-only preparer.  The default phase refreshes the
v16 ledger snapshot and provenance artifacts without freezing or launching a
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
V15 = ROOT / "data/autopilot_pair_delta_20260722_v15"
V16 = ROOT / "data/autopilot_pair_delta_20260722_v16"
GROUP_INPUT = V16 / "group_input.jsonl"
GROUP_SUMMARY = V16 / "group_input_summary.json"
CENSUS_INPUT = V16 / "census_input.jsonl"
INVENTORY = V16 / "exact_source_inventory.json"
PLAN = V16 / "provenance_plan.json"
PREFLIGHT = V16 / "preflight_audit_summary.json"
OUTPUT = V16 / "missing_pair_all.jsonl"

EXPECTED_TARGET_LABELS = 25_000
EXPECTED_TARGET_ROWS = 165_836
EXPECTED_V15_GROUP_ROWS = 13_323
EXPECTED_V15_OWNED_PAIRS = 21_942
EXPECTED_V15_DELTA_LABELS = 15
EXPECTED_V15_DELTA_PAIRS = 15
EXPECTED_V15_CONDITIONAL_ROUTES = 1
V15_ROUTE_SOURCE = ("24T10512", 16)
V15_ROUTE_TARGET = "24T11924"
V15_ROUTE_GOLD_R = {12, 16}

V15_PINS = {
    "prepare_v15_pair_delta.py":
        "b1d809b8ee0056e171d2c0921ed0fe3f5372d732cc3674fd6d6ee4a5f4830e7a",
    "run_v15_10512_11924.py":
        "fb1501d87306dbfe790118321d058a8d804a8fd213ba0024db2abdfba32920d0",
    "data/autopilot_pair_delta_20260722_v15/group_input.jsonl":
        "13aa01e6c96159fc6921637beedcf72f80be06295846c510f5cbb5df7cf2d149",
    "data/autopilot_pair_delta_20260722_v15/group_input_summary.json":
        "65747eac071172076279ba7b68633d64561fdf3f8d413636d7ce8428cfddbe6f",
    "data/autopilot_pair_delta_20260722_v15/census_input.jsonl":
        "a1c6abca203562e9666dc2704edbeff623e13ded5edbd58b32c6448c4e8a9a69",
    "data/autopilot_pair_delta_20260722_v15/missing_pair_all.jsonl":
        "a3ce1e40a49f8e300be594374f0b0e3530a8a8fce91077b76738824c6a12d07d",
    "data/autopilot_pair_delta_20260722_v15/exact_source_inventory.json":
        "ec7e1e41f7150d954e65c9ea6abeed106f4f693eefafb24ceef096d18ec2a78b",
    "data/autopilot_pair_delta_20260722_v15/provenance_plan.json":
        "76f735988320af0cda95df31edcad90d6253969b4b986a2515ebc8d585b2cf55",
    "data/autopilot_pair_delta_20260722_v15/preflight_audit_summary.json":
        "1ae4fa1dd448669929f6ff8061ae72010ffdd46c45e5f1c1d22305784bcfc0ad",
    "data/v15_10512_r16_pair_candidates.jsonl":
        "8688470bc42f9f37a0183130d7863abab414f53b1355521024248803d7e6bca2",
    "data/v15_10512_r16_frobenius_certificate.json":
        "1c24fbcbfd6f6b59e9ce30c4ed26dbd85eef9753ed2764a0e2caba693b434d29",
    "data/v15_10512_r16_to_11924_summary.json":
        "0008ba2980b2673d2b5aff1544e4085bb5095ad77774607ae71433bc8444e530",
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
    "data/autopilot_pair_delta_20260722_v15/missing_pair_all.jsonl",
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
        help="required exact post-v15 pair count when finalizing",
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
        raise ValueError(f"missing pinned v15 artifact: {relative}")
    actual = helper.sha256_path(path)
    if actual != V15_PINS[relative]:
        raise ValueError(f"pinned v15 artifact changed: {relative} ({actual})")


def validate_v15_boundary() -> tuple[list[dict], set[tuple[str, int]]]:
    """Validate the frozen v15 census and its sole exact route miss."""
    for relative in V15_PINS:
        require_pin(relative)

    group_rows = helper.read_jsonl(V15 / "group_input.jsonl")
    census_input_rows = helper.read_jsonl(V15 / "census_input.jsonl")
    completed_rows = helper.read_jsonl(V15 / "missing_pair_all.jsonl")
    summary = helper.read_json(V15 / "group_input_summary.json")
    inventory = helper.read_json(V15 / "exact_source_inventory.json")
    plan = helper.read_json(V15 / "provenance_plan.json")
    preflight = helper.read_json(V15 / "preflight_audit_summary.json")
    route_certificate = helper.read_json(
        ROOT / "data/v15_10512_r16_frobenius_certificate.json"
    )
    route_summary = helper.read_json(
        ROOT / "data/v15_10512_r16_to_11924_summary.json"
    )

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
    if len(group_rows) != EXPECTED_V15_GROUP_ROWS:
        raise ValueError("pinned v15 group-row cardinality mismatch")
    if len(owned_pairs) != EXPECTED_V15_OWNED_PAIRS:
        raise ValueError("pinned v15 owned-pair cardinality mismatch")
    if (
        len(census_input_rows) != EXPECTED_V15_GROUP_ROWS
        or len(completed_rows) != EXPECTED_V15_DELTA_LABELS
        or len(selected_pairs) != EXPECTED_V15_DELTA_PAIRS
        or selected_pairs != completed_pairs
        or selected_pairs != inventory_pairs
    ):
        raise ValueError("v15 completed census does not exactly cover its frozen delta")
    if any(
        row.get("status") != "certified" or not helper.certificate_is_exact(row)
        for row in completed_rows
    ):
        raise ValueError("v15 completed census contains an uncertified row")
    if (
        summary.get("schemaVersion") != "v15-refreshable-group-input-summary-v1"
        or summary.get("status") != "certified_boundary_confirmed"
        or summary.get("heavyPlanFinalized") is not True
        or int(summary.get("baseOwnedPairs", -1)) != 21_927
        or int(summary.get("snapshotOwnedPairs", -1)) != EXPECTED_V15_OWNED_PAIRS
        or int(summary.get("deltaPairs", -1)) != EXPECTED_V15_DELTA_PAIRS
        or int(summary.get("deltaLabels", -1)) != EXPECTED_V15_DELTA_LABELS
        or int(summary.get("targetLabels", -1)) != EXPECTED_TARGET_LABELS
        or int(summary.get("targetRows", -1)) != EXPECTED_TARGET_ROWS
    ):
        raise ValueError("v15 group summary envelope mismatch")
    if (
        inventory.get("schemaVersion") != "v15-accepted-scoreable-source-inventory-v1"
        or inventory.get("status") != "certified"
        or int(inventory.get("deltaPairCount", -1)) != EXPECTED_V15_DELTA_PAIRS
        or int(inventory.get("deltaLabelCount", -1)) != EXPECTED_V15_DELTA_LABELS
        or set((inventory.get("acceptedAnchorsByPair") or {}))
        != {pair_text(pair) for pair in selected_pairs}
    ):
        raise ValueError("v15 accepted-source inventory envelope mismatch")
    if (
        plan.get("schemaVersion") != "v15-pair-delta-provenance-plan-v1"
        or plan.get("status") != "ready_for_one_heavy_worker"
        or (plan.get("execution") or {}).get("heavyCommandFrozen") is not True
        or int((plan.get("delta") or {}).get("selectedSignatures", -1))
        != EXPECTED_V15_DELTA_PAIRS
    ):
        raise ValueError("v15 provenance plan envelope mismatch")
    checks = preflight.get("checks") or {}
    positive_checks = {
        "acceptedScoreableDeltaExact",
        "baselineCollisionCountZero",
        "coefficientAndCredentialScanClean",
        "outputAbsentGuardEnabled",
        "priorFrozenSignatureCollisionCountZero",
        "priorMapChainCompleteThroughV14",
        "provenanceAnchorsComplete",
        "signatureIsolationExact",
        "targetCacheCompleteAndUnique",
        "v14BoundaryPinned",
        "v14CompletedCensusExact",
        "v14LineageClosureTwoOfTwo",
    }
    if (
        preflight.get("schemaVersion") != "v15-light-preflight-audit-v1"
        or preflight.get("status") != "certified_safe_for_one_heavy_worker"
        or preflight.get("coefficientMaterialIncluded") is not False
        or preflight.get("credentialMaterialIncluded") is not False
        or any(checks.get(name) is not True for name in positive_checks)
    ):
        raise ValueError("v15 light preflight is not fully certified")

    conditional = [
        (row, route)
        for row in completed_rows
        for route in row.get("routes") or []
    ]
    if len(conditional) != EXPECTED_V15_CONDITIONAL_ROUTES:
        raise ValueError("v15 completed census does not have exactly one route")
    route_row, route = conditional[0]
    route_targets = [
        target
        for target in route_row.get("targets") or []
        if target.get("targetLabel") == V15_ROUTE_TARGET
    ]
    if (
        route_row.get("sourceLabel") != V15_ROUTE_SOURCE[0]
        or list(map(int, route_row.get("sourceR") or []))
        != [V15_ROUTE_SOURCE[1]]
        or route.get("targetLabel") != V15_ROUTE_TARGET
        or int(route.get("sourceR", -1)) != V15_ROUTE_SOURCE[1]
        or set(map(int, route.get("mappedTargetR") or [])) != {8, 16}
        or set(map(int, route.get("goldR") or [])) != V15_ROUTE_GOLD_R
        or route.get("deterministicTargetR") is not None
        or route.get("allCompatibleClassesGold") is not False
        or len(route_targets) != 1
        or int(route_targets[0].get("kernelOrder", -1)) != 1
    ):
        raise ValueError("v15 sole conditional-route envelope changed")

    certificate_rows = route_certificate.get("rows") or []
    certificate_summary = route_certificate.get("summary") or {}
    selection_pairs = (route_certificate.get("selection") or {}).get("sourcePairs") or []
    if (
        route_certificate.get("method")
        != "exact-unramified-frobenius-cycle-type-exclusion-v1"
        or route_certificate.get("inputSha256")
        != V15_PINS["data/v15_10512_r16_pair_candidates.jsonl"]
        or route_certificate.get("selectedInputRowsSha256")
        != V15_PINS["data/v15_10512_r16_pair_candidates.jsonl"]
        or selection_pairs != [
            {"label": V15_ROUTE_SOURCE[0], "r": V15_ROUTE_SOURCE[1]}
        ]
        or len(certificate_rows) != 1
        or certificate_rows[0].get("status") != "resolved"
        or certificate_rows[0].get("sourceLabel") != V15_ROUTE_SOURCE[0]
        or int(certificate_rows[0].get("sourceR", -1))
        != V15_ROUTE_SOURCE[1]
        or int(certificate_summary.get("rows", -1)) != 1
        or int(certificate_summary.get("resolved", -1)) != 1
        or int(certificate_summary.get("unresolved", -1)) != 0
        or int(certificate_summary.get("contradiction", -1)) != 0
    ):
        raise ValueError("v15 Frobenius route certificate is not exact and resolved")
    target_assignments = [
        assignment
        for assignment in certificate_rows[0].get("assignments") or []
        if assignment.get("targetLabel") == V15_ROUTE_TARGET
    ]
    if (
        len(target_assignments) != 1
        or int(target_assignments[0].get("targetR", -1)) != 8
        or route_summary.get("status") != "exact_signature_miss"
        or route_summary.get("targetLabel") != V15_ROUTE_TARGET
        or int(route_summary.get("realizedTargetR", -1)) != 8
        or int(route_summary.get("realizedTargetR", -1)) in V15_ROUTE_GOLD_R
        or route_summary.get("frobeniusCertificateSha256")
        != V15_PINS["data/v15_10512_r16_frobenius_certificate.json"]
        or route_summary.get("pairResultsSha256")
        != V15_PINS["data/v15_10512_r16_pair_candidates.jsonl"]
        or route_summary.get("coefficientMaterialIncluded") is not False
        or int(route_summary.get("workersLaunched", -1)) != 1
        or int(route_summary.get("submissionCalls", -1)) != 0
    ):
        raise ValueError("v15 sole route is not closed by the pinned exact r8 miss")
    return group_rows, owned_pairs


def validate_prior_maps(
    v15_pairs: set[tuple[str, int]],
) -> tuple[list[dict], set[tuple[str, int]]]:
    terminal = "data/autopilot_pair_delta_20260722_v15/missing_pair_all.jsonl"
    if PRIOR_MAPS[-1] != terminal:
        raise ValueError("prior-map chain does not terminate at v15")
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
    if not signature_pairs <= v15_pairs:
        raise ValueError("prior-map signature chain escapes the pinned v15 boundary")
    if artifacts[-1]["sha256"] != V15_PINS[terminal]:
        raise ValueError("terminal prior map is not the pinned completed v15 census")
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
            raise ValueError(f"post-v15 pair {label}:{r} has no exact accepted anchor")
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

    _, v15_pairs = validate_v15_boundary()
    prior_artifacts, prior_signature_pairs = validate_prior_maps(v15_pairs)
    snapshot = ledger_snapshot()
    connection = snapshot.pop("connection")
    try:
        baseline = snapshot["baseline"]
        accepted_pairs = snapshot["acceptedPairs"]
        if not v15_pairs <= accepted_pairs:
            raise ValueError("accepted ledger regressed behind the pinned v15 boundary")
        delta = accepted_pairs - v15_pairs
        if not delta:
            raise ValueError("post-v15 accepted-scoreable delta is empty")
        if delta & baseline:
            raise ValueError("post-v15 accepted delta intersects the baseline")
        if delta & prior_signature_pairs:
            raise ValueError("post-v15 accepted delta collides with a prior-map signature")
        if (
            args.expected_delta_pairs is not None
            and len(delta) != args.expected_delta_pairs
        ):
            raise ValueError(
                f"expected {args.expected_delta_pairs} post-v15 pairs, found {len(delta)}"
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
        raise ValueError("one or more post-v15 pairs are absent from the target cache")

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
        raise ValueError("v16 group input does not reproduce accepted-scoreable pairs")

    census_rows = []
    delta_labels = {label for label, _ in delta}
    for row in group_rows:
        selected = sorted(r for label, r in delta if label == row["label"])
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if helper.source_pairs(census_rows) != delta:
        raise ValueError("v16 census input does not exactly isolate the post-v15 delta")

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
        "groupInput": helper.relative_artifact(V15 / "group_input.jsonl"),
        "censusInput": helper.relative_artifact(V15 / "census_input.jsonl"),
        "completedCensus": helper.relative_artifact(V15 / "missing_pair_all.jsonl"),
        "preflight": helper.relative_artifact(V15 / "preflight_audit_summary.json"),
        "soleRoutePairCandidates": helper.relative_artifact(
            ROOT / "data/v15_10512_r16_pair_candidates.jsonl"
        ),
        "soleRouteFrobeniusCertificate": helper.relative_artifact(
            ROOT / "data/v15_10512_r16_frobenius_certificate.json"
        ),
        "soleRouteExactMissSummary": helper.relative_artifact(
            ROOT / "data/v15_10512_r16_to_11924_summary.json"
        ),
    }
    group_summary = {
        "schemaVersion": "v16-refreshable-group-input-summary-v1",
        "status": (
            "certified_boundary_confirmed" if args.finalize_heavy_plan
            else "prepared_awaiting_boundary_confirmation"
        ),
        "base": boundary_artifacts,
        "groupInput": {"path": str(GROUP_INPUT.relative_to(ROOT))},
        "rows": len(group_rows),
        "baseOwnedPairs": len(v15_pairs),
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
        "schemaVersion": "v16-accepted-scoreable-source-inventory-v1",
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
        "--prior-input", "data/autopilot_pair_delta_20260722_v15/group_input.jsonl",
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
        "schemaVersion": "v16-pair-delta-provenance-plan-v1",
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
                "completed pinned v15 boundary, 15/15 census rows, "
                "and 1/1 exact Frobenius route miss"
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
        "schemaVersion": "v16-light-preflight-audit-v1",
        "sealedAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_safe_for_one_heavy_worker" if args.finalize_heavy_plan
            else "certified_light_snapshot_awaiting_boundary_confirmation"
        ),
        "base": {
            "version": "v15",
            "frozenOwnedPairs": len(v15_pairs),
            "completedCensusRows": EXPECTED_V15_DELTA_LABELS,
            "closedConditionalRoutes": EXPECTED_V15_CONDITIONAL_ROUTES,
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
            "v15BoundaryPinned": True,
            "v15CompletedCensusExact": True,
            "v15SoleRouteExactMissClosure": True,
            "acceptedScoreableDeltaExact": True,
            "provenanceAnchorsComplete": len(anchors_by_pair) == len(delta),
            "targetCacheCompleteAndUnique": True,
            "signatureIsolationExact": helper.source_pairs(census_rows) == delta,
            "baselineCollisionCountZero": not bool(delta & baseline),
            "priorFrozenSignatureCollisionCountZero": not bool(delta & prior_signature_pairs),
            "priorMapChainCompleteThroughV15": len(prior_artifacts) == len(PRIOR_MAPS),
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
            "writesLimitedToV16Artifacts": True,
        },
    }
    if not all(audit["checks"].values()):
        raise ValueError("one or more v16 light preflight checks failed")
    assert_coefficient_free(audit, "preflight audit")
    helper.atomic_json(PREFLIGHT, audit)

    print(json.dumps({
        "status": plan["status"],
        "baseOwnedPairs": len(v15_pairs),
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
