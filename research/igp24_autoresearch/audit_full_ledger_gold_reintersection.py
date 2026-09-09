#!/usr/bin/env python3
"""Light-only full-ledger re-intersection with current zero-team targets.

The audit joins every accepted-scoreable ledger anchor to the complete pinned
unordered-pair action chain through v19 and its exact cached signature
profiles.  It also validates the allowlisted exact SINGLE, stable MULTI, and
Frobenius caches, then excludes every known ledger polynomial, filesystem or
ledger receipt, current text outbox, and already-processed pair-action anchor.

No polynomial arithmetic, Sage/GAP process, network call, submission, ledger
write, coefficient payload, or credential is used or emitted.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as pair_audit
import audit_low_contention_tc7_tc9_routes as exclusions
import prepare_v11_pair_delta as helper
import stage_all_exact_frobenius_unowned as all_exact
import stage_exact_shared_census as shared
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
OUTBOX = ROOT / "outbox"
V20_PLAN = DATA / "autopilot_pair_delta_20260722_v20/provenance_plan.json"
V20_CENSUS_INPUT = DATA / "autopilot_pair_delta_20260722_v20/census_input.jsonl"
V20_OUTPUT = DATA / "autopilot_pair_delta_20260722_v20/missing_pair_all.jsonl"
PROFILE_BACKFILL_PLAN = (
    DATA / "gold_profile_backfill_20260722_batch1/provenance_plan.json"
)
PROFILE_BACKFILL_OUTPUT = (
    DATA / "gold_profile_backfill_20260722_batch1/missing_profile_rows.jsonl"
)
PROFILE_BACKFILL_BATCH2_PLAN = (
    DATA / "gold_profile_backfill_20260722_batch2/provenance_plan.json"
)
PROFILE_BACKFILL_BATCH2_OUTPUT = (
    DATA / "gold_profile_backfill_20260722_batch2/missing_profile_rows.jsonl"
)
PROFILE_BACKFILL_BATCH3_PLAN = (
    DATA / "gold_profile_backfill_20260722_batch3/provenance_plan.json"
)
PROFILE_BACKFILL_BATCH3_OUTPUT = (
    DATA / "gold_profile_backfill_20260722_batch3/missing_profile_rows.jsonl"
)
PROFILE_BACKFILL_BATCH4_PLAN = (
    DATA / "gold_profile_backfill_20260722_batch4/provenance_plan.json"
)
PROFILE_BACKFILL_BATCH4_OUTPUT = (
    DATA / "gold_profile_backfill_20260722_batch4/missing_profile_rows.jsonl"
)
PROFILE_BACKFILL_BATCH5_PLAN = (
    DATA / "gold_profile_backfill_20260722_batch5/provenance_plan.json"
)
PROFILE_BACKFILL_BATCH5_OUTPUT = (
    DATA / "gold_profile_backfill_20260722_batch5/missing_profile_rows.jsonl"
)
PROFILE_BACKFILL_BATCH6_PLAN = (
    DATA / "gold_profile_backfill_20260722_batch6/provenance_plan.json"
)
PROFILE_BACKFILL_BATCH6_OUTPUT = (
    DATA / "gold_profile_backfill_20260722_batch6/missing_profile_rows.jsonl"
)
PROFILE_BACKFILL_BATCH7_PLAN = (
    DATA / "gold_profile_backfill_20260722_batch7/provenance_plan.json"
)
PROFILE_BACKFILL_BATCH7_OUTPUT = (
    DATA / "gold_profile_backfill_20260722_batch7/missing_profile_rows.jsonl"
)
V21_OUTPUT = DATA / "autopilot_pair_delta_20260722_v21/missing_pair_all.jsonl"
V21_PLAN = DATA / "autopilot_pair_delta_20260722_v21/provenance_plan.json"
V21_CENSUS_INPUT = DATA / "autopilot_pair_delta_20260722_v21/census_input.jsonl"
CONDITIONAL_CALIBRATION = DATA / "conditional_factor_calibration_20260722_certificate.json"
CERTIFICATE = DATA / "full_ledger_gold_reintersection_certificate.json"
SUMMARY = DATA / "full_ledger_gold_reintersection_summary.json"

EXPECTED_TARGET_ROWS = 165_836
EXPECTED_TARGET_LABELS = 25_000
EXPECTED_PRIOR_MAPS = 23
SEALED_V19_ACCEPTED_PAIRS = 22_028


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_replace(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def artifact(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT)),
        "sha256": helper.sha256_path(path),
    }


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def pair_key(pair: tuple[str, int]) -> tuple[int, int]:
    return pair_audit.pair_sort_key(pair)


def logical_digest(rows: list[object]) -> str:
    return pair_audit.canonical_digest(rows)


def fraction_text(value: Fraction) -> str:
    return pair_audit.fraction_text(value)


def file_boundary(paths: list[Path]) -> dict:
    rows = []
    for path in sorted({item.resolve() for item in paths}):
        if not path.is_file():
            continue
        rows.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": helper.sha256_path(path),
                "sizeBytes": path.stat().st_size,
            }
        )
    return {
        "files": len(rows),
        "indexSha256": logical_digest(rows),
        "rows": rows,
    }


def volatile_boundary() -> dict:
    return {
        "receipts": file_boundary(list(RECEIPTS.glob("sub_*.json"))),
        "outboxes": file_boundary(list(OUTBOX.glob("*.txt"))),
    }


def compact_boundary(value: dict) -> dict:
    return {
        name: {
            "files": int(row["files"]),
            "indexSha256": str(row["indexSha256"]),
        }
        for name, row in value.items()
    }


def load_actions_and_profiles() -> tuple:
    plan = read_json(V20_PLAN)
    prior_maps = list(((plan.get("artifacts") or {}).get("priorMaps") or []))
    if (
        plan.get("status") != "ready_for_one_heavy_worker"
        or len(prior_maps) != EXPECTED_PRIOR_MAPS
        or (plan.get("execution") or {}).get("heavyWorkerLaunched") is not False
    ):
        raise ValueError("v20 frozen prior-map plan is not intact")

    action_artifacts = list(prior_maps)
    v20_output_complete = False
    profile_backfill_complete = False
    profile_backfill_signatures = 0
    profile_backfill_batch2_complete = False
    profile_backfill_batch2_signatures = 0
    profile_backfill_batch3_complete = False
    profile_backfill_batch3_signatures = 0
    profile_backfill_batch4_complete = False
    profile_backfill_batch4_signatures = 0
    profile_backfill_batch5_complete = False
    profile_backfill_batch5_signatures = 0
    profile_backfill_batch6_complete = False
    profile_backfill_batch6_signatures = 0
    profile_backfill_batch7_complete = False
    profile_backfill_batch7_signatures = 0
    v21_output_complete = False
    v21_signatures = 0
    if V20_OUTPUT.is_file():
        selected = helper.source_pairs(pair_audit.read_jsonl(V20_CENSUS_INPUT))
        completed_rows = pair_audit.read_jsonl(V20_OUTPUT)
        completed = {
            (str(row["sourceLabel"]), int(r))
            for row in completed_rows
            for r in row.get("sourceR") or []
        }
        if (
            len(selected) != 2
            or len(completed_rows) != 2
            or completed != selected
            or any(
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
                for row in completed_rows
            )
        ):
            raise ValueError("present v20 pair census is incomplete or inexact")
        action_artifacts.append(helper.relative_artifact(V20_OUTPUT))
        v20_output_complete = True

    if PROFILE_BACKFILL_OUTPUT.is_file():
        backfill_plan = read_json(PROFILE_BACKFILL_PLAN)
        selected = {str(value) for value in backfill_plan.get("selectedSignatures") or []}
        completed_rows = pair_audit.read_jsonl(PROFILE_BACKFILL_OUTPUT)
        completed = {
            f"{row['sourceLabel']}/r{int(r)}"
            for row in completed_rows
            for r in row.get("sourceR") or []
        }
        if (
            backfill_plan.get("status")
            != "ready_waiting_for_root_heavy_clearance"
            or len(selected) != 32
            or len(completed_rows) != 29
            or completed != selected
            or any(
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
                for row in completed_rows
            )
        ):
            raise ValueError("present profile-backfill output is incomplete or inexact")
        action_artifacts.append(helper.relative_artifact(PROFILE_BACKFILL_OUTPUT))
        profile_backfill_complete = True
        profile_backfill_signatures = len(completed)

    if PROFILE_BACKFILL_BATCH2_OUTPUT.is_file():
        if not profile_backfill_complete:
            raise ValueError("profile-backfill batch2 cannot precede exact batch1 coverage")
        backfill_plan = read_json(PROFILE_BACKFILL_BATCH2_PLAN)
        selected = {str(value) for value in backfill_plan.get("selectedSignatures") or []}
        excluded_batch1 = {
            str(value)
            for value in backfill_plan.get("excludedEarlierBatchSignatures") or []
        }
        completed_rows = pair_audit.read_jsonl(PROFILE_BACKFILL_BATCH2_OUTPUT)
        completed = {
            f"{row['sourceLabel']}/r{int(r)}"
            for row in completed_rows
            for r in row.get("sourceR") or []
        }
        batch1_plan = read_json(PROFILE_BACKFILL_PLAN)
        batch1_selected = {
            str(value) for value in batch1_plan.get("selectedSignatures") or []
        }
        batch1_artifact = helper.relative_artifact(PROFILE_BACKFILL_OUTPUT)
        sealed_profiles = list(
            ((backfill_plan.get("artifacts") or {}).get("profileArtifactsAtSeal") or [])
        )
        if (
            backfill_plan.get("status")
            != "ready_waiting_for_root_heavy_clearance"
            or int(backfill_plan.get("batchNumber", -1)) != 2
            or len(selected) != 35
            or len(completed_rows) != 25
            or completed != selected
            or selected & batch1_selected
            or excluded_batch1 != batch1_selected
            or batch1_artifact not in sealed_profiles
            or any(
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
                for row in completed_rows
            )
        ):
            raise ValueError("present profile-backfill batch2 output is incomplete or inexact")
        action_artifacts.append(helper.relative_artifact(PROFILE_BACKFILL_BATCH2_OUTPUT))
        profile_backfill_batch2_complete = True
        profile_backfill_batch2_signatures = len(completed)

    if V21_OUTPUT.is_file():
        v21_plan = read_json(V21_PLAN)
        v21_prior_maps = list(
            ((v21_plan.get("artifacts") or {}).get("priorMaps") or [])
        )
        selected = helper.source_pairs(pair_audit.read_jsonl(V21_CENSUS_INPUT))
        completed_rows = pair_audit.read_jsonl(V21_OUTPUT)
        completed = {
            (str(row["sourceLabel"]), int(r))
            for row in completed_rows
            for r in row.get("sourceR") or []
        }
        if (
            v21_plan.get("status") != "ready_for_one_heavy_worker"
            or len(v21_prior_maps) != 24
            or (v21_plan.get("execution") or {}).get("heavyWorkerLaunched") is not False
            or len(selected) != 32
            or len(completed_rows) != 29
            or completed != selected
            or sum(len(row.get("routes") or []) for row in completed_rows) != 0
            or any(
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
                for row in completed_rows
            )
        ):
            raise ValueError("present v21 pair census is incomplete or inexact")
        action_artifacts.append(helper.relative_artifact(V21_OUTPUT))
        v21_output_complete = True
        v21_signatures = len(completed)

    if PROFILE_BACKFILL_BATCH3_OUTPUT.is_file():
        if not (
            profile_backfill_complete
            and profile_backfill_batch2_complete
            and v21_output_complete
        ):
            raise ValueError(
                "profile-backfill batch3 cannot precede exact batch1/batch2/v21 coverage"
            )
        backfill_plan = read_json(PROFILE_BACKFILL_BATCH3_PLAN)
        selected = {
            str(value) for value in backfill_plan.get("selectedSignatures") or []
        }
        excluded_earlier = {
            str(value)
            for value in backfill_plan.get("excludedEarlierBatchSignatures") or []
        }
        completed_rows = pair_audit.read_jsonl(PROFILE_BACKFILL_BATCH3_OUTPUT)
        completed = {
            f"{row['sourceLabel']}/r{int(r)}"
            for row in completed_rows
            for r in row.get("sourceR") or []
        }
        batch1_selected = {
            str(value)
            for value in read_json(PROFILE_BACKFILL_PLAN).get("selectedSignatures") or []
        }
        batch2_selected = {
            str(value)
            for value in read_json(PROFILE_BACKFILL_BATCH2_PLAN).get(
                "selectedSignatures"
            )
            or []
        }
        required_sealed_artifacts = {
            tuple(sorted(helper.relative_artifact(path).items()))
            for path in (
                PROFILE_BACKFILL_OUTPUT,
                PROFILE_BACKFILL_BATCH2_OUTPUT,
                V21_OUTPUT,
            )
        }
        sealed_profiles = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get(
                    "profileArtifactsAtSeal"
                )
                or []
            )
        }
        sealed_actions = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get("actionMaps") or []
            )
        }
        if (
            backfill_plan.get("status")
            != "ready_waiting_for_root_heavy_clearance"
            or int(backfill_plan.get("batchNumber", -1)) != 3
            or len(selected) != 36
            or len(completed_rows) != 10
            or completed != selected
            or selected & (batch1_selected | batch2_selected)
            or excluded_earlier != (batch1_selected | batch2_selected)
            or not required_sealed_artifacts <= sealed_profiles
            or not required_sealed_artifacts <= sealed_actions
            or any(
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
                for row in completed_rows
            )
        ):
            raise ValueError(
                "present profile-backfill batch3 output is incomplete or inexact"
            )
        action_artifacts.append(helper.relative_artifact(PROFILE_BACKFILL_BATCH3_OUTPUT))
        profile_backfill_batch3_complete = True
        profile_backfill_batch3_signatures = len(completed)

    if PROFILE_BACKFILL_BATCH4_OUTPUT.is_file():
        if not (
            profile_backfill_complete
            and profile_backfill_batch2_complete
            and profile_backfill_batch3_complete
            and v21_output_complete
        ):
            raise ValueError(
                "profile-backfill batch4 cannot precede exact batch1/batch2/batch3/v21 coverage"
            )
        backfill_plan = read_json(PROFILE_BACKFILL_BATCH4_PLAN)
        selected = {
            str(value) for value in backfill_plan.get("selectedSignatures") or []
        }
        excluded_earlier = {
            str(value)
            for value in backfill_plan.get("excludedEarlierBatchSignatures") or []
        }
        completed_rows = pair_audit.read_jsonl(PROFILE_BACKFILL_BATCH4_OUTPUT)
        completed = {
            f"{row['sourceLabel']}/r{int(r)}"
            for row in completed_rows
            for r in row.get("sourceR") or []
        }
        earlier_selected = set()
        for earlier_plan in (
            PROFILE_BACKFILL_PLAN,
            PROFILE_BACKFILL_BATCH2_PLAN,
            PROFILE_BACKFILL_BATCH3_PLAN,
        ):
            earlier_selected.update(
                str(value)
                for value in read_json(earlier_plan).get("selectedSignatures") or []
            )
        required_sealed_artifacts = {
            tuple(sorted(helper.relative_artifact(path).items()))
            for path in (
                PROFILE_BACKFILL_OUTPUT,
                PROFILE_BACKFILL_BATCH2_OUTPUT,
                PROFILE_BACKFILL_BATCH3_OUTPUT,
                V21_OUTPUT,
            )
        }
        sealed_profiles = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get(
                    "profileArtifactsAtSeal"
                )
                or []
            )
        }
        sealed_actions = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get("actionMaps") or []
            )
        }
        if (
            backfill_plan.get("status")
            != "ready_waiting_for_root_heavy_clearance"
            or int(backfill_plan.get("batchNumber", -1)) != 4
            or len(selected) != 36
            or len(completed_rows) != 7
            or completed != selected
            or selected & earlier_selected
            or excluded_earlier != earlier_selected
            or not required_sealed_artifacts <= sealed_profiles
            or not required_sealed_artifacts <= sealed_actions
            or any(
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
                for row in completed_rows
            )
        ):
            raise ValueError(
                "present profile-backfill batch4 output is incomplete or inexact"
            )
        action_artifacts.append(helper.relative_artifact(PROFILE_BACKFILL_BATCH4_OUTPUT))
        profile_backfill_batch4_complete = True
        profile_backfill_batch4_signatures = len(completed)

    if PROFILE_BACKFILL_BATCH5_OUTPUT.is_file():
        if not (
            profile_backfill_complete
            and profile_backfill_batch2_complete
            and profile_backfill_batch3_complete
            and profile_backfill_batch4_complete
            and v21_output_complete
        ):
            raise ValueError(
                "profile-backfill batch5 cannot precede exact batches1-4/v21 coverage"
            )
        backfill_plan = read_json(PROFILE_BACKFILL_BATCH5_PLAN)
        selected = {
            str(value) for value in backfill_plan.get("selectedSignatures") or []
        }
        excluded_earlier = {
            str(value)
            for value in backfill_plan.get("excludedEarlierBatchSignatures") or []
        }
        completed_rows = pair_audit.read_jsonl(PROFILE_BACKFILL_BATCH5_OUTPUT)
        completed = {
            f"{row['sourceLabel']}/r{int(r)}"
            for row in completed_rows
            for r in row.get("sourceR") or []
        }
        earlier_plans = (
            PROFILE_BACKFILL_PLAN,
            PROFILE_BACKFILL_BATCH2_PLAN,
            PROFILE_BACKFILL_BATCH3_PLAN,
            PROFILE_BACKFILL_BATCH4_PLAN,
        )
        earlier_outputs = (
            PROFILE_BACKFILL_OUTPUT,
            PROFILE_BACKFILL_BATCH2_OUTPUT,
            PROFILE_BACKFILL_BATCH3_OUTPUT,
            PROFILE_BACKFILL_BATCH4_OUTPUT,
            V21_OUTPUT,
        )
        earlier_selected = {
            str(value)
            for path in earlier_plans
            for value in read_json(path).get("selectedSignatures") or []
        }
        required_sealed_artifacts = {
            tuple(sorted(helper.relative_artifact(path).items()))
            for path in earlier_outputs
        }
        sealed_profiles = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get(
                    "profileArtifactsAtSeal"
                )
                or []
            )
        }
        sealed_actions = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get("actionMaps") or []
            )
        }
        if (
            backfill_plan.get("status")
            != "ready_waiting_for_root_heavy_clearance"
            or int(backfill_plan.get("batchNumber", -1)) != 5
            or len(selected) != 32
            or len(completed_rows) != 8
            or completed != selected
            or selected & earlier_selected
            or excluded_earlier != earlier_selected
            or not required_sealed_artifacts <= sealed_profiles
            or not required_sealed_artifacts <= sealed_actions
            or any(
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
                for row in completed_rows
            )
        ):
            raise ValueError(
                "present profile-backfill batch5 output is incomplete or inexact"
            )
        action_artifacts.append(helper.relative_artifact(PROFILE_BACKFILL_BATCH5_OUTPUT))
        profile_backfill_batch5_complete = True
        profile_backfill_batch5_signatures = len(completed)

    if PROFILE_BACKFILL_BATCH6_OUTPUT.is_file():
        if not (
            profile_backfill_complete
            and profile_backfill_batch2_complete
            and profile_backfill_batch3_complete
            and profile_backfill_batch4_complete
            and profile_backfill_batch5_complete
            and v21_output_complete
        ):
            raise ValueError(
                "profile-backfill batch6 cannot precede exact batches1-5/v21 coverage"
            )
        backfill_plan = read_json(PROFILE_BACKFILL_BATCH6_PLAN)
        selected = {
            str(value) for value in backfill_plan.get("selectedSignatures") or []
        }
        excluded_earlier = {
            str(value)
            for value in backfill_plan.get("excludedEarlierBatchSignatures") or []
        }
        completed_rows = pair_audit.read_jsonl(PROFILE_BACKFILL_BATCH6_OUTPUT)
        completed = {
            f"{row['sourceLabel']}/r{int(r)}"
            for row in completed_rows
            for r in row.get("sourceR") or []
        }
        earlier_plans = (
            PROFILE_BACKFILL_PLAN,
            PROFILE_BACKFILL_BATCH2_PLAN,
            PROFILE_BACKFILL_BATCH3_PLAN,
            PROFILE_BACKFILL_BATCH4_PLAN,
            PROFILE_BACKFILL_BATCH5_PLAN,
        )
        earlier_outputs = (
            PROFILE_BACKFILL_OUTPUT,
            PROFILE_BACKFILL_BATCH2_OUTPUT,
            PROFILE_BACKFILL_BATCH3_OUTPUT,
            PROFILE_BACKFILL_BATCH4_OUTPUT,
            PROFILE_BACKFILL_BATCH5_OUTPUT,
            V21_OUTPUT,
        )
        earlier_selected = {
            str(value)
            for path in earlier_plans
            for value in read_json(path).get("selectedSignatures") or []
        }
        required_sealed_artifacts = {
            tuple(sorted(helper.relative_artifact(path).items()))
            for path in earlier_outputs
        }
        sealed_profiles = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get(
                    "profileArtifactsAtSeal"
                )
                or []
            )
        }
        sealed_actions = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get("actionMaps") or []
            )
        }
        if (
            backfill_plan.get("status")
            != "ready_waiting_for_root_heavy_clearance"
            or int(backfill_plan.get("batchNumber", -1)) != 6
            or len(selected) != 50
            or len(completed_rows) != 10
            or completed != selected
            or selected & earlier_selected
            or excluded_earlier != earlier_selected
            or not required_sealed_artifacts <= sealed_profiles
            or not required_sealed_artifacts <= sealed_actions
            or any(
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
                for row in completed_rows
            )
        ):
            raise ValueError(
                "present profile-backfill batch6 output is incomplete or inexact"
            )
        action_artifacts.append(helper.relative_artifact(PROFILE_BACKFILL_BATCH6_OUTPUT))
        profile_backfill_batch6_complete = True
        profile_backfill_batch6_signatures = len(completed)

    if PROFILE_BACKFILL_BATCH7_OUTPUT.is_file():
        if not (
            profile_backfill_complete
            and profile_backfill_batch2_complete
            and profile_backfill_batch3_complete
            and profile_backfill_batch4_complete
            and profile_backfill_batch5_complete
            and profile_backfill_batch6_complete
            and v21_output_complete
        ):
            raise ValueError(
                "profile-backfill batch7 cannot precede exact batches1-6/v21 coverage"
            )
        backfill_plan = read_json(PROFILE_BACKFILL_BATCH7_PLAN)
        selected = {
            str(value) for value in backfill_plan.get("selectedSignatures") or []
        }
        excluded_earlier = {
            str(value)
            for value in backfill_plan.get("excludedEarlierBatchSignatures") or []
        }
        completed_rows = pair_audit.read_jsonl(PROFILE_BACKFILL_BATCH7_OUTPUT)
        completed = {
            f"{row['sourceLabel']}/r{int(r)}"
            for row in completed_rows
            for r in row.get("sourceR") or []
        }
        earlier_plans = (
            PROFILE_BACKFILL_PLAN,
            PROFILE_BACKFILL_BATCH2_PLAN,
            PROFILE_BACKFILL_BATCH3_PLAN,
            PROFILE_BACKFILL_BATCH4_PLAN,
            PROFILE_BACKFILL_BATCH5_PLAN,
            PROFILE_BACKFILL_BATCH6_PLAN,
        )
        earlier_outputs = (
            PROFILE_BACKFILL_OUTPUT,
            PROFILE_BACKFILL_BATCH2_OUTPUT,
            PROFILE_BACKFILL_BATCH3_OUTPUT,
            PROFILE_BACKFILL_BATCH4_OUTPUT,
            PROFILE_BACKFILL_BATCH5_OUTPUT,
            PROFILE_BACKFILL_BATCH6_OUTPUT,
            V21_OUTPUT,
        )
        earlier_selected = {
            str(value)
            for path in earlier_plans
            for value in read_json(path).get("selectedSignatures") or []
        }
        required_sealed_artifacts = {
            tuple(sorted(helper.relative_artifact(path).items()))
            for path in earlier_outputs
        }
        sealed_profiles = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get(
                    "profileArtifactsAtSeal"
                )
                or []
            )
        }
        sealed_actions = {
            tuple(sorted(row.items()))
            for row in (
                (backfill_plan.get("artifacts") or {}).get("actionMaps") or []
            )
        }
        if (
            backfill_plan.get("status")
            != "ready_waiting_for_root_heavy_clearance"
            or int(backfill_plan.get("batchNumber", -1)) != 7
            or len(selected) != 50
            or len(completed_rows) != 10
            or completed != selected
            or selected & earlier_selected
            or excluded_earlier != earlier_selected
            or not required_sealed_artifacts <= sealed_profiles
            or not required_sealed_artifacts <= sealed_actions
            or any(
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
                for row in completed_rows
            )
        ):
            raise ValueError(
                "present profile-backfill batch7 output is incomplete or inexact"
            )
        action_artifacts.append(helper.relative_artifact(PROFILE_BACKFILL_BATCH7_OUTPUT))
        profile_backfill_batch7_complete = True
        profile_backfill_batch7_signatures = len(completed)

    actions: dict[str, dict] = {}
    provenance: dict[str, dict] = {}
    row_count = 0
    duplicate_labels = set()
    for item in action_artifacts:
        path = ROOT / str(item["path"])
        digest = helper.sha256_path(path)
        if digest != str(item["sha256"]):
            raise ValueError(f"pinned pair-action map changed: {path}")
        for row in pair_audit.read_jsonl(path):
            row_count += 1
            label = str(row["sourceLabel"])
            incumbent = actions.get(label)
            if incumbent is not None:
                duplicate_labels.add(label)
                if pair_audit.action_core(incumbent) != pair_audit.action_core(row):
                    raise ValueError(f"conflicting exact pair actions: {label}")
            actions[label] = row
            provenance[label] = {"path": str(path.relative_to(ROOT)), "sha256": digest}

    profiles, profile_provenance, profile_artifacts = pair_audit.load_profiles(
        actions, action_artifacts
    )
    audit = {
        "priorMaps": len(prior_maps),
        "actionMapsIncludingCompletedV20": len(action_artifacts),
        "v20OutputComplete": v20_output_complete,
        "v21OutputComplete": v21_output_complete,
        "v21Signatures": v21_signatures,
        "v21PriorMapsThroughV20": 24 if v21_output_complete else 0,
        "profileBackfillOutputComplete": profile_backfill_complete,
        "profileBackfillSignatures": profile_backfill_signatures,
        "profileBackfillBatch1OutputComplete": profile_backfill_complete,
        "profileBackfillBatch1Signatures": profile_backfill_signatures,
        "profileBackfillBatch2OutputComplete": profile_backfill_batch2_complete,
        "profileBackfillBatch2Signatures": profile_backfill_batch2_signatures,
        "profileBackfillBatch3OutputComplete": profile_backfill_batch3_complete,
        "profileBackfillBatch3Signatures": profile_backfill_batch3_signatures,
        "profileBackfillBatch4OutputComplete": profile_backfill_batch4_complete,
        "profileBackfillBatch4Signatures": profile_backfill_batch4_signatures,
        "profileBackfillBatch5OutputComplete": profile_backfill_batch5_complete,
        "profileBackfillBatch5Signatures": profile_backfill_batch5_signatures,
        "profileBackfillBatch6OutputComplete": profile_backfill_batch6_complete,
        "profileBackfillBatch6Signatures": profile_backfill_batch6_signatures,
        "profileBackfillBatch7OutputComplete": profile_backfill_batch7_complete,
        "profileBackfillBatch7Signatures": profile_backfill_batch7_signatures,
        "profileBackfillCombinedSignatures": (
            profile_backfill_signatures
            + profile_backfill_batch2_signatures
            + profile_backfill_batch3_signatures
            + profile_backfill_batch4_signatures
            + profile_backfill_batch5_signatures
            + profile_backfill_batch6_signatures
            + profile_backfill_batch7_signatures
        ),
        "actionRows": row_count,
        "distinctActionLabels": len(actions),
        "duplicateConsistentActionLabels": len(duplicate_labels),
        "conflictingActionLabels": 0,
        "profileLabels": len(profiles),
        "profileSourceSignatures": len(profile_provenance),
        "priorMapIndexSha256": logical_digest(prior_maps),
    }
    return (
        actions,
        provenance,
        action_artifacts,
        profiles,
        profile_provenance,
        profile_artifacts,
        audit,
    )


def ledger_snapshot(connection: sqlite3.Connection) -> dict:
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        )
    }
    known_pairs = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE label IS NOT NULL AND r IS NOT NULL"
        )
    }
    targets = {
        (str(row["label"]), int(row["r"])): {
            "teamCount": int(row["team_count"]),
            "discovered": bool(row["discovered"]),
            "minimumDiscAbs": (
                str(row["minimum_disc_abs"])
                if row["minimum_disc_abs"] is not None
                else None
            ),
            "generatedAt": str(row["generated_at"]),
        }
        for row in connection.execute(
            "SELECT label,r,team_count,discovered,minimum_disc_abs,generated_at "
            "FROM targets"
        )
    }
    if (
        len(targets) != EXPECTED_TARGET_ROWS
        or len({label for label, _r in targets}) != EXPECTED_TARGET_LABELS
    ):
        raise ValueError("local target snapshot is incomplete")
    raw_gold = {
        pair
        for pair, state in targets.items()
        if int(state["teamCount"]) == 0
        and pair not in baseline
        and pair not in owned
    }
    gold = raw_gold - known_pairs
    anchor_rows = int(
        connection.execute(
            "SELECT COUNT(*) FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        ).fetchone()[0]
    )
    accepted_rows = [
        [label, r] for label, r in sorted(owned, key=pair_key)
    ]
    target_rows = [
        [
            label,
            r,
            state["teamCount"],
            state["discovered"],
            state["minimumDiscAbs"],
            state["generatedAt"],
        ]
        for (label, r), state in sorted(targets.items(), key=lambda item: pair_key(item[0]))
    ]
    return {
        "baseline": baseline,
        "owned": owned,
        "knownPairs": known_pairs,
        "targets": targets,
        "rawGold": raw_gold,
        "gold": gold,
        "acceptedAnchorRows": anchor_rows,
        "acceptedPairSetSha256": logical_digest(accepted_rows),
        "targetSnapshotSha256": logical_digest(target_rows),
    }


def merge_exact_claim(pool: dict[str, dict], claim: dict) -> None:
    digest = str(claim["digest"])
    pair = tuple(claim["pair"])
    incumbent = pool.get(digest)
    if incumbent is None:
        pool[digest] = {
            "digest": digest,
            "pair": pair,
            "coefficientBytes": int(claim["coefficientBytes"]),
            "fieldDiscriminants": set(claim.get("fieldDiscriminants") or []),
            "polynomialDiscriminants": set(
                claim.get("polynomialDiscriminants") or []
            ),
            "families": set(claim.get("families") or []),
            "proofArtifacts": set(claim.get("proofArtifacts") or []),
            "sourceKeys": set(claim.get("sourceKeys") or []),
            "sourceHashes": set(claim.get("sourceHashes") or []),
        }
        return
    if incumbent["pair"] != pair:
        raise ValueError(f"one exact hash has conflicting target pairs: {digest}")
    incumbent["coefficientBytes"] = min(
        incumbent["coefficientBytes"], int(claim["coefficientBytes"])
    )
    for field in (
        "fieldDiscriminants",
        "polynomialDiscriminants",
        "families",
        "proofArtifacts",
        "sourceKeys",
        "sourceHashes",
    ):
        incumbent[field].update(claim.get(field) or [])


def exact_and_lineage_corpus(connection: sqlite3.Connection) -> dict:
    candidates, single_corpus = single.scan_candidates(DATA.resolve())
    valid_single = [
        candidate
        for candidate in candidates
        if single.validate_source_pins(connection, candidate)
    ]

    stable_pool, stable_audit = shared.scan_stable_multi(
        DATA.resolve(), (DATA / "pair_signature_map.jsonl").resolve()
    )
    valid_stable = {}
    invalid_stable_sources = 0
    stable_evidence = {}
    for digest, row in stable_pool.items():
        evidence = {
            key: shared.source_evidence(connection, key)
            for key in row["sourceKeys"]
        }
        if all(value is not None for value in evidence.values()):
            valid_stable[digest] = row
            stable_evidence.update(evidence)
        else:
            invalid_stable_sources += 1

    frobenius_certificates, staged_pairs = all_exact.scan_json_artifacts(DATA.resolve())
    frobenius_pool, frobenius_certificate_audit, frobenius_audit = (
        all_exact.collect_exact_pool(
            frobenius_certificates, connection, ROOT, None
        )
    )
    unresolved_pool, unresolved_audit = shared.unresolved_options(
        frobenius_certificates, ROOT
    )

    exact_pool: dict[str, dict] = {}
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    processed_keys: set[tuple[str, int]] = set()
    processed_hashes: set[str] = set()
    processed_facts = Counter()

    # Every exact proof occurrence contributes to conservative receipt/outbox
    # pair mapping.  Only source-revalidated occurrences can become hits or
    # close an anchor as processed lineage.
    for candidate in candidates:
        pair_index[str(candidate["coefficientSha256"])].add(
            (str(candidate["targetLabel"]), int(candidate["targetR"]))
        )

    for candidate in valid_single:
        pins = candidate.get("sourcePins") or []
        keys = {
            (str(pin["submissionId"]), int(pin["polynomialIndex"]))
            for pin in pins
        }
        source_hashes = {
            str(pin["coefficientSha256"])
            for pin in pins
            if pin.get("coefficientSha256") is not None
        }
        schema = str((candidate.get("proof") or {}).get("schema"))
        merge_exact_claim(
            exact_pool,
            {
                "digest": candidate["coefficientSha256"],
                "pair": (candidate["targetLabel"], int(candidate["targetR"])),
                "coefficientBytes": candidate["coefficientBytes"],
                "fieldDiscriminants": (
                    [int(candidate["fieldDiscriminantAbs"])]
                    if candidate.get("fieldDiscriminantAbs") is not None
                    else []
                ),
                "polynomialDiscriminants": (
                    [int(candidate["polynomialDiscriminantAbs"])]
                    if candidate.get("polynomialDiscriminantAbs") is not None
                    else []
                ),
                "families": [schema],
                "proofArtifacts": [str((candidate.get("proof") or {}).get("artifact"))],
                "sourceKeys": keys,
                "sourceHashes": source_hashes,
            },
        )
        if schema == "pair_sum_single_v1":
            processed_keys.update(keys)
            processed_hashes.update(source_hashes)
            processed_facts["exactSinglePairActionOccurrences"] += 1

    for digest, row in valid_stable.items():
        pair = tuple(row["pair"])
        pair_index[digest].add(pair)
        keys = set(row["sourceKeys"])
        source_hashes = {
            str(stable_evidence[key]["coefficientSha256"]) for key in keys
        }
        merge_exact_claim(
            exact_pool,
            {
                "digest": digest,
                "pair": pair,
                "coefficientBytes": row["coefficientBytes"],
                "fieldDiscriminants": (
                    [int(row["fieldDiscriminantAbs"])]
                    if row.get("fieldDiscriminantAbs") is not None
                    else []
                ),
                "polynomialDiscriminants": [int(row["polynomialDiscriminantAbs"])],
                "families": list(row["families"]),
                "proofArtifacts": [str(proof["artifact"]) for proof in row["proofs"]],
                "sourceKeys": keys,
                "sourceHashes": source_hashes,
            },
        )
        processed_keys.update((key[0], key[1]) for key in keys)
        processed_hashes.update(source_hashes)
        processed_facts["stableMultiExactHashes"] += 1

    for digest, row in frobenius_pool.items():
        pair = tuple(row["pair"])
        pair_index[digest].add(pair)
        keys = {
            (str(proof["sourceSubmissionId"]), int(proof["sourcePolynomialIndex"]))
            for proof in row["proofs"]
        }
        evidence_rows = {
            key: connection.execute(
                "SELECT p.coefficient_hash FROM polynomials p JOIN verifications v "
                "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
                "AND p.polynomial_index=? AND v.status='accepted' AND v.scoreable=1",
                key,
            ).fetchone()
            for key in keys
        }
        if any(value is None for value in evidence_rows.values()):
            raise ValueError("validated Frobenius proof lost accepted source provenance")
        source_hashes = {str(value[0]) for value in evidence_rows.values()}
        merge_exact_claim(
            exact_pool,
            {
                "digest": digest,
                "pair": pair,
                "coefficientBytes": row["coefficientBytes"],
                "fieldDiscriminants": list(row["fieldDiscriminants"]),
                "polynomialDiscriminants": [int(row["polynomialDiscriminantAbs"])],
                "families": ["exact_frobenius_assignment_v1"],
                "proofArtifacts": [str(proof["artifact"]) for proof in row["proofs"]],
                "sourceKeys": keys,
                "sourceHashes": source_hashes,
            },
        )
        processed_keys.update(keys)
        processed_hashes.update(source_hashes)
        processed_facts["exactFrobeniusHashes"] += 1

    for digest, row in unresolved_pool.items():
        key4 = row["sourceKey"]
        evidence = shared.source_evidence(connection, key4)
        if evidence is None:
            continue
        processed_keys.add((str(key4[0]), int(key4[1])))
        processed_hashes.add(str(evidence["coefficientSha256"]))
        processed_facts["unresolvedFrobeniusHashes"] += 1
        for pair in row["possiblePairs"]:
            pair_index[digest].add(tuple(pair))

    supplemental, supplemental_artifacts = exclusions.supplemental_exact_pair_index()
    for digest, pairs in supplemental.items():
        pair_index[digest].update(pairs)

    return {
        "singleCandidates": candidates,
        "validSingle": valid_single,
        "singleCorpus": single_corpus,
        "stablePool": valid_stable,
        "stableAudit": stable_audit,
        "invalidStableSources": invalid_stable_sources,
        "frobeniusCertificates": frobenius_certificates,
        "frobeniusPool": frobenius_pool,
        "frobeniusCertificateAudit": frobenius_certificate_audit,
        "frobeniusAudit": frobenius_audit,
        "stagedPairs": staged_pairs,
        "unresolvedPool": unresolved_pool,
        "unresolvedAudit": unresolved_audit,
        "exactPool": exact_pool,
        "pairIndex": pair_index,
        "processedKeys": processed_keys,
        "processedHashes": processed_hashes,
        "processedFacts": dict(sorted(processed_facts.items())),
        "supplementalArtifacts": supplemental_artifacts,
    }


def receipt_and_outbox_exclusions(
    connection: sqlite3.Connection, corpus: dict
) -> dict:
    pair_index = corpus["pairIndex"]
    single_hashes, single_pairs, single_audit = single.receipt_exclusions(
        RECEIPTS.resolve(), DATA.resolve(), connection, pair_index
    )
    minimal_exact_pool = {
        digest: {"pair": next(iter(pairs))}
        for digest, pairs in pair_index.items()
        if len(pairs) == 1
    }
    broad_hashes, broad_pairs, broad_audit = all_exact.receipt_exclusions(
        connection,
        RECEIPTS.resolve(),
        corpus["stagedPairs"],
        minimal_exact_pool,
        ROOT,
    )
    receipt_hashes = single_hashes | broad_hashes
    receipt_pairs = single_pairs | broad_pairs
    outbox_hashes, outbox_pairs, outbox_audit = exclusions.outbox_exclusions(
        pair_index, connection
    )
    return {
        "receiptHashes": receipt_hashes,
        "receiptPairs": receipt_pairs,
        "outboxHashes": outbox_hashes,
        "outboxPairs": outbox_pairs,
        "singleReceiptAudit": single_audit,
        "broadReceiptAudit": broad_audit,
        "outboxAudit": outbox_audit,
    }


def exact_hits(snapshot: dict, corpus: dict, excluded: dict, connection: sqlite3.Connection) -> dict:
    pair_index = corpus["pairIndex"]
    ambiguous = {digest for digest, pairs in pair_index.items() if len(pairs) != 1}
    known_hashes = single.query_known_hashes(connection, set(pair_index))
    blocked_hashes = (
        known_hashes
        | excluded["receiptHashes"]
        | excluded["outboxHashes"]
        | ambiguous
    )
    blocked_pairs = excluded["receiptPairs"] | excluded["outboxPairs"]

    eligible = []
    for digest, row in corpus["exactPool"].items():
        pair = tuple(row["pair"])
        if (
            digest in blocked_hashes
            or pair in blocked_pairs
            or pair not in snapshot["gold"]
        ):
            continue
        eligible.append(row)

    by_pair: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in eligible:
        by_pair[tuple(row["pair"])].append(row)

    selected = []
    alternatives = 0
    for pair in sorted(by_pair, key=pair_key):
        rows = by_pair[pair]
        field_complete = all(row["fieldDiscriminants"] for row in rows)
        infinity = 10**100000
        ordered = sorted(
            rows,
            key=lambda row: (
                min(row["fieldDiscriminants"]) if field_complete else infinity,
                min(row["polynomialDiscriminants"] or {infinity}),
                int(row["coefficientBytes"]),
                str(row["digest"]),
            ),
        )
        winner = ordered[0]
        alternatives += len(ordered) - 1
        state = snapshot["targets"][pair]
        selected.append(
            {
                "rank": len(selected) + 1,
                "targetPair": pair_text(pair),
                "teamCountAtAudit": 0,
                "minimumDiscAbsAtAudit": state["minimumDiscAbs"],
                "coefficientSha256": winner["digest"],
                "coefficientBytes": winner["coefficientBytes"],
                "fieldDiscriminantAbs": (
                    str(min(winner["fieldDiscriminants"]))
                    if winner["fieldDiscriminants"]
                    else None
                ),
                "polynomialDiscriminantAbs": (
                    str(min(winner["polynomialDiscriminants"]))
                    if winner["polynomialDiscriminants"]
                    else None
                ),
                "proofFamilies": sorted(winner["families"]),
                "proofArtifacts": sorted(winner["proofArtifacts"]),
                "sourceAnchorCount": len(winner["sourceKeys"]),
                "dedupeCandidateHashes": len(ordered),
                "certainty": "already_exact_validated",
                "projectedMarginalScoreUpper": "1",
            }
        )
    return {
        "selected": selected,
        "eligibleHashes": len(eligible),
        "dedupedAlternativeHashes": alternatives,
        "ambiguousPairClaimHashes": len(ambiguous),
        "knownLedgerHashes": len(known_hashes),
        "knownHashes": known_hashes,
    }


def build_anchor_inventory(
    connection: sqlite3.Connection,
    processed_keys: set[tuple[str, int]],
    processed_hashes: set[str],
) -> tuple[dict[tuple[str, int], dict], dict]:
    inventory: dict[tuple[str, int], dict] = {}
    total = 0
    processed_total = 0
    for row in connection.execute(
        "SELECT v.submission_id,v.polynomial_index,v.label,v.r,p.coefficient_hash,"
        "length(p.coefficients) AS coefficient_bytes "
        "FROM verifications v JOIN polynomials p "
        "USING(submission_id,polynomial_index) WHERE v.status='accepted' "
        "AND v.scoreable=1"
    ):
        total += 1
        key = (str(row["submission_id"]), int(row["polynomial_index"]))
        source_pair = (str(row["label"]), int(row["r"]))
        digest = str(row["coefficient_hash"])
        state = inventory.setdefault(
            source_pair,
            {"total": 0, "processed": 0, "fresh": 0, "bestFresh": None},
        )
        state["total"] += 1
        if key in processed_keys or digest in processed_hashes:
            state["processed"] += 1
            processed_total += 1
            continue
        state["fresh"] += 1
        candidate = {
            "submissionId": key[0],
            "polynomialIndex": key[1],
            "coefficientSha256": digest,
            "coefficientBytes": int(row["coefficient_bytes"]),
        }
        incumbent = state["bestFresh"]
        if incumbent is None or (
            candidate["coefficientBytes"],
            candidate["submissionId"],
            candidate["polynomialIndex"],
        ) < (
            incumbent["coefficientBytes"],
            incumbent["submissionId"],
            incumbent["polynomialIndex"],
        ):
            state["bestFresh"] = candidate
    if any(row["total"] != row["processed"] + row["fresh"] for row in inventory.values()):
        raise ValueError("accepted anchor inventory does not partition exactly")
    return inventory, {
        "acceptedScoreableAnchorRowsAudited": total,
        "processedPairActionAnchorRowsExcluded": processed_total,
        "freshAcceptedScoreableAnchorRows": total - processed_total,
        "acceptedScoreableSignaturesWithAnchors": len(inventory),
    }


def enumerate_routes(
    connection: sqlite3.Connection,
    snapshot: dict,
    actions: dict[str, dict],
    action_provenance: dict[str, dict],
    profiles: dict[str, dict],
    profile_provenance: dict[tuple[str, int], set[str]],
    corpus: dict,
    excluded: dict,
    anchor_inventory: dict[tuple[str, int], dict],
) -> tuple[list[dict], dict]:
    eligible = (
        snapshot["gold"]
        - excluded["receiptPairs"]
        - excluded["outboxPairs"]
    )
    missing_action_pairs = set()
    action_pairs = set()
    no_length24_pairs = set()
    identity_pairs = set()
    profile_pairs = set()
    unresolved_profile_pairs = set()
    routes = []
    route_signatures_without_fresh_anchor = 0

    for source_pair in sorted(snapshot["owned"], key=pair_key):
        source_label, source_r = source_pair
        action = actions.get(source_label)
        if action is None:
            missing_action_pairs.add(source_pair)
            continue
        if int(action.get("length24OrbitCount", 0)) < 1:
            no_length24_pairs.add(source_pair)
            continue
        action_pairs.add(source_pair)
        action_targets = {
            int(target["orbitIndex"]): str(target["targetLabel"])
            for target in action.get("targets") or []
        }
        if source_r == 24:
            compatible = [
                {
                    "classIndex": -1,
                    "classSize": 1,
                    "sourceR": 24,
                    "targets": {
                        orbit_index: (label, 24)
                        for orbit_index, label in action_targets.items()
                    },
                }
            ]
            proof_kind = "identity_complex_conjugation"
            identity_pairs.add(source_pair)
        else:
            compatible = [
                row
                for (r, _class_index), row in profiles.get(source_label, {}).items()
                if int(r) == source_r
            ]
            proof_kind = "exact_cached_conjugacy_profiles"
            if compatible:
                profile_pairs.add(source_pair)
            else:
                unresolved_profile_pairs.add(source_pair)
                continue
        if any(set(row["targets"]) != set(action_targets) for row in compatible):
            raise ValueError(f"incomplete cached pair profile: {pair_text(source_pair)}")

        anchor_state = anchor_inventory[source_pair]
        anchor = anchor_state["bestFresh"]

        for orbit_index, target_label in sorted(action_targets.items()):
            outcome_mass: Counter = Counter()
            for row in compatible:
                pair = tuple(row["targets"][orbit_index])
                if pair[0] != target_label:
                    raise ValueError("profile target label differs from action map")
                outcome_mass[pair] += int(row["classSize"])
            outcomes = set(outcome_mass)
            gold_outcomes = outcomes & eligible
            if not gold_outcomes:
                continue
            if anchor is None:
                route_signatures_without_fresh_anchor += 1
                continue
            mass_total = sum(outcome_mass.values())
            success_mass = sum(outcome_mass[pair] for pair in gold_outcomes)
            deterministic = len(outcomes) == 1
            single_orbit = len(action_targets) == 1
            all_compatible = outcomes <= eligible
            if deterministic and single_orbit:
                classification = "deterministic_single_orbit"
            elif all_compatible:
                classification = "all_compatible_safe"
            else:
                classification = "conditional"
            routes.append(
                {
                    "classification": classification,
                    "sourcePair": source_pair,
                    "orbitIndex": orbit_index,
                    "orbitCount": len(action_targets),
                    "outcomes": outcomes,
                    "goldOutcomes": gold_outcomes,
                    "outcomeMass": outcome_mass,
                    "successMass": success_mass,
                    "totalMass": mass_total,
                    "compatibleClassCount": len(compatible),
                    "proofKind": proof_kind,
                    "actionArtifact": action_provenance[source_label],
                    "profileArtifacts": sorted(
                        profile_provenance.get(source_pair, set())
                    ),
                    "anchor": anchor,
                    "freshAnchorCount": int(anchor_state["fresh"]),
                    "receiptOutcomeCollisions": outcomes & excluded["receiptPairs"],
                    "outboxOutcomeCollisions": outcomes & excluded["outboxPairs"],
                }
            )

    coverage = {
        "eligibleCurrentGoldPairs": len(eligible),
        "acceptedScoreablePairs": len(snapshot["owned"]),
        "actionMappedAcceptedPairs": len(action_pairs),
        "acceptedPairsWithNoLength24PairAction": len(no_length24_pairs),
        "signatureResolvedByIdentity": len(identity_pairs),
        "signatureResolvedByExactProfile": len(profile_pairs),
        "signatureUnresolvedNoProfile": len(unresolved_profile_pairs),
        "acceptedPairsMissingActionLabel": len(missing_action_pairs),
        "goldFacingRouteSignaturesWithoutFreshAnchor": route_signatures_without_fresh_anchor,
        "missingActionPairs": [
            pair_text(pair) for pair in sorted(missing_action_pairs, key=pair_key)
        ],
        "unresolvedProfilePairSetSha256": logical_digest(
            [[label, r] for label, r in sorted(unresolved_profile_pairs, key=pair_key)]
        ),
    }
    return routes, coverage


def route_rank(row: dict, snapshot: dict) -> tuple:
    success = Fraction(int(row["successMass"]), int(row["totalMass"]))
    minimum = min(
        int(snapshot["targets"][pair]["minimumDiscAbs"] or 10**1000)
        for pair in row["goldOutcomes"]
    )
    return (
        -success,
        len(row["outcomes"]),
        int(row["anchor"]["coefficientBytes"]),
        minimum,
        pair_key(row["sourcePair"]),
        int(row["orbitIndex"]),
    )


def public_routes(routes: list[dict], snapshot: dict) -> dict:
    by_class = defaultdict(list)
    for row in routes:
        by_class[row["classification"]].append(row)

    public_by_class = {}
    route_lookup = {}
    pair_route_ids: dict[tuple[str, int], list[str]] = defaultdict(list)
    prefixes = {
        "deterministic_single_orbit": "dso",
        "all_compatible_safe": "acs",
        "conditional": "cond",
    }
    for classification in (
        "deterministic_single_orbit",
        "all_compatible_safe",
        "conditional",
    ):
        ordered = sorted(by_class[classification], key=lambda row: route_rank(row, snapshot))
        public = []
        seen_route_keys = set()
        for row in ordered:
            route_key = (
                row["sourcePair"],
                int(row["orbitIndex"]),
                tuple(sorted(row["outcomes"], key=pair_key)),
            )
            if route_key in seen_route_keys:
                continue
            seen_route_keys.add(route_key)
            route_id = f"{prefixes[classification]}_{len(public)+1:04d}"
            success = Fraction(int(row["successMass"]), int(row["totalMass"]))
            outcome_pairs = [
                pair_text(pair) for pair in sorted(row["outcomes"], key=pair_key)
            ]
            gold_pairs = [
                pair_text(pair) for pair in sorted(row["goldOutcomes"], key=pair_key)
            ]
            item = {
                "routeId": route_id,
                "rank": len(public) + 1,
                "certaintyClass": classification,
                "sourcePair": pair_text(row["sourcePair"]),
                "sourceAnchor": row["anchor"],
                "freshAnchorCountForSourceSignature": row["freshAnchorCount"],
                "orbitIndex": row["orbitIndex"],
                "length24OrbitCount": row["orbitCount"],
                "compatibleClassCount": row["compatibleClassCount"],
                "compatibleSuccessMass": f"{row['successMass']}/{row['totalMass']}",
                "conditionalSuccessFractionExact": fraction_text(success),
                "projectedMarginalScoreUpper": fraction_text(success),
                "possibleTargetPairs": outcome_pairs,
                "currentGoldTargetPairs": gold_pairs,
                "proofKind": row["proofKind"],
                "actionArtifact": row["actionArtifact"],
                "profileArtifacts": row["profileArtifacts"],
                "receiptOutcomeCollisions": [
                    pair_text(pair)
                    for pair in sorted(row["receiptOutcomeCollisions"], key=pair_key)
                ],
                "outboxOutcomeCollisions": [
                    pair_text(pair)
                    for pair in sorted(row["outboxOutcomeCollisions"], key=pair_key)
                ],
                "heavyWorkerLaunched": False,
                "submissionAuthorized": False,
            }
            public.append(item)
            route_lookup[route_id] = row
            for pair in row["goldOutcomes"]:
                pair_route_ids[pair].append(route_id)
        public_by_class[classification] = public

    target_index = []
    for pair in sorted(pair_route_ids, key=pair_key):
        route_ids = pair_route_ids[pair]
        best = min(
            route_ids,
            key=lambda route_id: route_rank(route_lookup[route_id], snapshot),
        )
        state = snapshot["targets"][pair]
        target_index.append(
            {
                "pair": pair_text(pair),
                "teamCountAtAudit": 0,
                "minimumDiscAbsAtAudit": state["minimumDiscAbs"],
                "bestRouteId": best,
                "routeCount": len(route_ids),
            }
        )
    return {
        "byClass": public_by_class,
        "targetIndex": target_index,
        "rawRoutes": len(routes),
        "deduplicatedRoutes": sum(len(rows) for rows in public_by_class.values()),
    }


def unresolved_saved_classification(
    snapshot: dict, corpus: dict, excluded: dict, exact: dict
) -> dict:
    blocked_hashes = (
        excluded["receiptHashes"]
        | excluded["outboxHashes"]
        | exact["knownHashes"]
    )
    blocked_pairs = (
        excluded["receiptPairs"]
        | excluded["outboxPairs"]
        | snapshot["baseline"]
        | snapshot["owned"]
        | snapshot["knownPairs"]
    )
    all_compatible = []
    conditional = []
    for digest, row in corpus["unresolvedPool"].items():
        possible = set(row["possiblePairs"])
        if digest in blocked_hashes or possible & blocked_pairs:
            continue
        gold = possible & snapshot["gold"]
        if not gold:
            continue
        item = {
            "coefficientSha256": digest,
            "coefficientBytes": int(row["coefficientBytes"]),
            "possibleTargetPairs": [
                pair_text(pair) for pair in sorted(possible, key=pair_key)
            ],
            "currentGoldTargetPairs": [
                pair_text(pair) for pair in sorted(gold, key=pair_key)
            ],
            "proofArtifact": str((row.get("proof") or {}).get("artifact")),
            "certainty": (
                "saved_all_compatible_exact_option_set"
                if possible <= snapshot["gold"]
                else "saved_conditional_exact_option_set"
            ),
        }
        (all_compatible if possible <= snapshot["gold"] else conditional).append(item)
    for rows in (all_compatible, conditional):
        rows.sort(
            key=lambda row: (
                len(row["possibleTargetPairs"]),
                int(row["coefficientBytes"]),
                str(row["coefficientSha256"]),
            )
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
    return {"allCompatible": all_compatible, "conditional": conditional}


def main() -> int:
    calibration = read_json(CONDITIONAL_CALIBRATION)
    if (
        calibration.get("status")
        != "sealed_biased_conditional_frontier_no_automatic_heavy_execution"
        or (calibration.get("decision") or {}).get("automaticExecutionPaused") is not True
        or not all((calibration.get("checks") or {}).values())
    ):
        raise ValueError("conditional calibration pause certificate is not intact")
    (
        actions,
        action_provenance,
        action_artifacts,
        profiles,
        profile_provenance,
        profile_artifacts,
        action_audit,
    ) = load_actions_and_profiles()

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        snapshot = ledger_snapshot(connection)
        if PROFILE_BACKFILL_BATCH2_OUTPUT.is_file():
            batch2_plan = read_json(PROFILE_BACKFILL_BATCH2_PLAN)
            batch2_boundary = batch2_plan.get("boundary") or {}
            if (
                int(batch2_boundary.get("acceptedScoreablePairs", -1))
                != len(snapshot["owned"])
                or int(batch2_boundary.get("acceptedScoreableAnchorRows", -1))
                != snapshot["acceptedAnchorRows"]
                or str(batch2_boundary.get("acceptedPairSetSha256"))
                != snapshot["acceptedPairSetSha256"]
                or int(batch2_boundary.get("targetRows", -1))
                != len(snapshot["targets"])
                or str(batch2_boundary.get("targetSnapshotSha256"))
                != snapshot["targetSnapshotSha256"]
            ):
                raise ValueError("profile-backfill batch2 plan boundary differs from ledger")
        if PROFILE_BACKFILL_BATCH3_OUTPUT.is_file():
            batch3_plan = read_json(PROFILE_BACKFILL_BATCH3_PLAN)
            batch3_boundary = batch3_plan.get("boundary") or {}
            if (
                int(batch3_boundary.get("acceptedScoreablePairs", -1))
                != len(snapshot["owned"])
                or int(batch3_boundary.get("acceptedScoreableAnchorRows", -1))
                != snapshot["acceptedAnchorRows"]
                or str(batch3_boundary.get("acceptedPairSetSha256"))
                != snapshot["acceptedPairSetSha256"]
                or int(batch3_boundary.get("targetRows", -1))
                != len(snapshot["targets"])
                or str(batch3_boundary.get("targetSnapshotSha256"))
                != snapshot["targetSnapshotSha256"]
            ):
                raise ValueError("profile-backfill batch3 plan boundary differs from ledger")
        if PROFILE_BACKFILL_BATCH4_OUTPUT.is_file():
            batch4_plan = read_json(PROFILE_BACKFILL_BATCH4_PLAN)
            batch4_boundary = batch4_plan.get("boundary") or {}
            if (
                int(batch4_boundary.get("acceptedScoreablePairs", -1))
                != len(snapshot["owned"])
                or int(batch4_boundary.get("acceptedScoreableAnchorRows", -1))
                != snapshot["acceptedAnchorRows"]
                or str(batch4_boundary.get("acceptedPairSetSha256"))
                != snapshot["acceptedPairSetSha256"]
                or int(batch4_boundary.get("targetRows", -1))
                != len(snapshot["targets"])
                or str(batch4_boundary.get("targetSnapshotSha256"))
                != snapshot["targetSnapshotSha256"]
            ):
                raise ValueError("profile-backfill batch4 plan boundary differs from ledger")
        if PROFILE_BACKFILL_BATCH5_OUTPUT.is_file():
            batch5_plan = read_json(PROFILE_BACKFILL_BATCH5_PLAN)
            batch5_boundary = batch5_plan.get("boundary") or {}
            if (
                int(batch5_boundary.get("acceptedScoreablePairs", -1))
                != len(snapshot["owned"])
                or int(batch5_boundary.get("acceptedScoreableAnchorRows", -1))
                != snapshot["acceptedAnchorRows"]
                or str(batch5_boundary.get("acceptedPairSetSha256"))
                != snapshot["acceptedPairSetSha256"]
                or int(batch5_boundary.get("targetRows", -1))
                != len(snapshot["targets"])
                or str(batch5_boundary.get("targetSnapshotSha256"))
                != snapshot["targetSnapshotSha256"]
            ):
                raise ValueError("profile-backfill batch5 plan boundary differs from ledger")
        if PROFILE_BACKFILL_BATCH6_OUTPUT.is_file():
            batch6_plan = read_json(PROFILE_BACKFILL_BATCH6_PLAN)
            batch6_boundary = batch6_plan.get("boundary") or {}
            if (
                int(batch6_boundary.get("acceptedScoreablePairs", -1))
                != len(snapshot["owned"])
                or int(batch6_boundary.get("acceptedScoreableAnchorRows", -1))
                != snapshot["acceptedAnchorRows"]
                or str(batch6_boundary.get("acceptedPairSetSha256"))
                != snapshot["acceptedPairSetSha256"]
                or int(batch6_boundary.get("targetRows", -1))
                != len(snapshot["targets"])
                or str(batch6_boundary.get("targetSnapshotSha256"))
                != snapshot["targetSnapshotSha256"]
            ):
                raise ValueError("profile-backfill batch6 plan boundary differs from ledger")
        if PROFILE_BACKFILL_BATCH7_OUTPUT.is_file():
            batch7_plan = read_json(PROFILE_BACKFILL_BATCH7_PLAN)
            batch7_boundary = batch7_plan.get("boundary") or {}
            if (
                int(batch7_boundary.get("acceptedScoreablePairs", -1))
                != len(snapshot["owned"])
                or int(batch7_boundary.get("acceptedScoreableAnchorRows", -1))
                != snapshot["acceptedAnchorRows"]
                or str(batch7_boundary.get("acceptedPairSetSha256"))
                != snapshot["acceptedPairSetSha256"]
                or int(batch7_boundary.get("targetRows", -1))
                != len(snapshot["targets"])
                or str(batch7_boundary.get("targetSnapshotSha256"))
                != snapshot["targetSnapshotSha256"]
            ):
                raise ValueError("profile-backfill batch7 plan boundary differs from ledger")
        corpus = exact_and_lineage_corpus(connection)
        anchor_inventory, anchor_audit = build_anchor_inventory(
            connection, corpus["processedKeys"], corpus["processedHashes"]
        )
        if (
            set(anchor_inventory) != snapshot["owned"]
            or anchor_audit["acceptedScoreableAnchorRowsAudited"]
            != snapshot["acceptedAnchorRows"]
        ):
            raise ValueError("full accepted anchor inventory differs from ledger snapshot")
        before = volatile_boundary()
        excluded = receipt_and_outbox_exclusions(connection, corpus)
        exact = exact_hits(snapshot, corpus, excluded, connection)
        routes, coverage = enumerate_routes(
            connection,
            snapshot,
            actions,
            action_provenance,
            profiles,
            profile_provenance,
            corpus,
            excluded,
            anchor_inventory,
        )
        coverage.update(anchor_audit)
        public = public_routes(routes, snapshot)
        saved_unresolved = unresolved_saved_classification(
            snapshot, corpus, excluded, exact
        )
        after = volatile_boundary()
        if compact_boundary(before) != compact_boundary(after):
            raise ValueError("receipt/outbox boundary changed during audit")
    finally:
        connection.close()

    deterministic = public["byClass"]["deterministic_single_orbit"]
    all_safe = public["byClass"]["all_compatible_safe"]
    conditional = public["byClass"]["conditional"]
    immediate_count = (
        len(exact["selected"])
        + len(saved_unresolved["allCompatible"])
        + len(deterministic)
        + len(all_safe)
    )
    zero_immediate = immediate_count == 0
    target_index_pairs = [row["pair"] for row in public["targetIndex"]]
    if len(target_index_pairs) != len(set(target_index_pairs)):
        raise ValueError("gold target index is not pair-deduplicated")

    receipt_audit = excluded["singleReceiptAudit"]
    broad_receipt = excluded["broadReceiptAudit"]
    outbox_audit = excluded["outboxAudit"]
    missing_action_pairs = coverage["missingActionPairs"]
    certificate = {
        "schemaVersion": "full-ledger-gold-reintersection-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_zero_immediate_gold_frontier_conditional_only"
            if zero_immediate and conditional
            else "certified_zero_full_frontier"
            if zero_immediate
            else "certified_fresh_gold_candidates_found"
        ),
        "scope": (
            "current teamCount=0 nonbaseline unowned targets intersected with all "
            "accepted-scoreable anchors, sealed pair actions/profiles, validated exact "
            "saved outputs, receipts, outboxes, and processed exact lineage"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "boundary": {
            "database": {"path": str(DB.relative_to(ROOT))},
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "acceptedScoreableLabels": len({label for label, _r in snapshot["owned"]}),
            "acceptedScoreableAnchorRows": snapshot["acceptedAnchorRows"],
            "acceptedPairSetSha256": snapshot["acceptedPairSetSha256"],
            "targetRows": len(snapshot["targets"]),
            "targetLabels": len({label for label, _r in snapshot["targets"]}),
            "targetSnapshotSha256": snapshot["targetSnapshotSha256"],
            "rawCurrentZeroTeamNonbaselineUnownedPairs": len(snapshot["rawGold"]),
            "processedKnownPairExclusions": len(snapshot["rawGold"] - snapshot["gold"]),
            "eligibleCurrentGoldPairsBeforeReceiptOutbox": len(snapshot["gold"]),
            "receiptAndOutboxBoundary": compact_boundary(after),
        },
        "sealedPairCorpus": {
            **action_audit,
            "v20Plan": artifact(V20_PLAN),
            "actionMaps": action_artifacts,
            "profileArtifacts": profile_artifacts,
        },
        "conditionalExecutionPolicy": {
            "status": "paused_by_exact_historical_calibration",
            "automaticHeavyExecutionAuthorized": False,
            "calibration": artifact(CONDITIONAL_CALIBRATION),
            "strictResolvedPackets": int(
                calibration["strictPacketCensus"]["independentlyFactoredConditionalPackets"]
            ),
            "freshGoldSuccesses": int(
                calibration["strictPacketCensus"]["freshGoldSuccesses"]
            ),
        },
        "validatedSavedCorpus": {
            "singleProofOccurrences": len(corpus["singleCandidates"]),
            "sourceValidatedSingleProofOccurrences": len(corpus["validSingle"]),
            "singleScanner": corpus["singleCorpus"],
            "stableExactHashes": len(corpus["stablePool"]),
            "stableAudit": corpus["stableAudit"],
            "invalidStableSourceRows": corpus["invalidStableSources"],
            "frobeniusExactHashes": len(corpus["frobeniusPool"]),
            "frobeniusAudit": corpus["frobeniusAudit"],
            "frobeniusCertificates": len(corpus["frobeniusCertificates"]),
            "unresolvedExactOptionHashes": len(corpus["unresolvedPool"]),
            "unresolvedAudit": corpus["unresolvedAudit"],
            "mergedExactHashes": len(corpus["exactPool"]),
            "exactPairIndexHashes": len(corpus["pairIndex"]),
            "supplementalPairMapArtifacts": corpus["supplementalArtifacts"],
        },
        "exclusions": {
            "receiptHashes": len(excluded["receiptHashes"]),
            "receiptPairs": len(excluded["receiptPairs"]),
            "filesystemReceipts": receipt_audit["receiptCount"],
            "broadSubmissionReceiptTableRows": broad_receipt["submissionReceiptTableRows"],
            "unsyncedReceiptCount": len(broad_receipt["unsyncedSubmissionIds"]),
            "outboxHashes": len(excluded["outboxHashes"]),
            "outboxPairs": len(excluded["outboxPairs"]),
            "outboxFiles": outbox_audit["outboxFiles"],
            "outboxNonemptyFiles": outbox_audit["nonemptyOutboxFiles"],
            "outboxIndexSha256": outbox_audit["artifactIndexSha256"],
            "processedPairActionSourceKeys": len(corpus["processedKeys"]),
            "processedPairActionSourceHashes": len(corpus["processedHashes"]),
            "processedLineageFacts": corpus["processedFacts"],
            "knownExactCandidateHashes": exact["knownLedgerHashes"],
            "ambiguousExactPairClaimHashes": exact["ambiguousPairClaimHashes"],
        },
        "coverage": coverage,
        "classifications": {
            "alreadyExactHits": exact["selected"],
            "savedAllCompatibleExactOptionSets": saved_unresolved["allCompatible"],
            "savedConditionalExactOptionSets": saved_unresolved["conditional"],
            "deterministicSingleOrbitRunbooks": deterministic,
            "allCompatibleSafeRoutes": all_safe,
            "conditionalRoutes": conditional,
            "conditionalTargetIndex": public["targetIndex"],
        },
        "deduplication": {
            "alreadyExactEligibleHashes": exact["eligibleHashes"],
            "alreadyExactSelectedPairs": len(exact["selected"]),
            "alreadyExactAlternativeHashes": exact["dedupedAlternativeHashes"],
            "rawGoldFacingRoutes": public["rawRoutes"],
            "deduplicatedGoldFacingRoutes": public["deduplicatedRoutes"],
            "deduplicatedGoldFacingTargetPairs": len(public["targetIndex"]),
        },
        "ranking": {
            "certaintyOrder": [
                "already exact validated output",
                "saved all-compatible exact option set",
                "deterministic single-orbit fresh-anchor route",
                "all-compatible safe fresh-anchor route",
                "conditional fresh-anchor route",
            ],
            "withinRouteClass": [
                "higher exact compatible-class success fraction",
                "fewer possible output pairs",
                "shorter accepted source encoding",
                "smaller current target minimum discriminant",
                "source pair and orbit order",
            ],
            "upsideQualifier": (
                "teamCount=0 contributes score upper bound 1 before discriminant penalty; "
                "conditional routes multiply this by exact compatible-class mass"
            ),
        },
        "staleOrIncompleteCoverage": {
            "currentAcceptedPairsBeyondSealedV19Boundary": (
                len(snapshot["owned"]) - SEALED_V19_ACCEPTED_PAIRS
            ),
            "missingExactActionPairs": missing_action_pairs,
            "missingExactActionPairCount": len(missing_action_pairs),
            "missingExactProfilePairCount": coverage["signatureUnresolvedNoProfile"],
            "missingExactProfilePairSetSha256": coverage["unresolvedProfilePairSetSha256"],
            "v20CensusOutputAbsent": not V20_OUTPUT.exists(),
            "v21CensusOutputAbsent": not V21_OUTPUT.exists(),
            "profileBackfillBatch3OutputAbsent": (
                not PROFILE_BACKFILL_BATCH3_OUTPUT.exists()
            ),
            "profileBackfillBatch4OutputAbsent": (
                not PROFILE_BACKFILL_BATCH4_OUTPUT.exists()
            ),
            "profileBackfillBatch5OutputAbsent": (
                not PROFILE_BACKFILL_BATCH5_OUTPUT.exists()
            ),
            "profileBackfillBatch6OutputAbsent": (
                not PROFILE_BACKFILL_BATCH6_OUTPUT.exists()
            ),
            "profileBackfillBatch7OutputAbsent": (
                not PROFILE_BACKFILL_BATCH7_OUTPUT.exists()
            ),
            "recommendedRebuilds": (
                [
                    {
                        "kind": "pair_action_delta",
                        "scope": "guarded v21 thirty-two-signature census",
                        "reason": "current accepted signatures lack a sealed pair-action row",
                        "heavyClearanceRequired": True,
                    }
                ]
                if missing_action_pairs
                else []
            ) + [
                {
                    "kind": "pair_signature_profile_backfill",
                    "scopePairCount": coverage["signatureUnresolvedNoProfile"],
                    "reason": (
                        "action-mapped accepted signatures without an exact cached compatible-"
                        "class profile were conservatively excluded from route promotion"
                    ),
                    "heavyClearanceRequired": True,
                },
            ],
        },
        "zeroCertificate": {
            "immediateExactOrGuaranteedSafeFrontierExhausted": zero_immediate,
            "immediateCandidateCount": immediate_count,
            "alreadyExactHits": len(exact["selected"]),
            "savedAllCompatibleExactOptionSets": len(saved_unresolved["allCompatible"]),
            "deterministicSingleOrbitRunbooks": len(deterministic),
            "allCompatibleSafeRoutes": len(all_safe),
            "conditionalRoutesRemain": len(conditional),
            "conditionalDistinctTargets": len(public["targetIndex"]),
            "scopeQualifier": (
                "zero applies to the complete currently sealed exact corpus only; unresolved "
                "profile signatures and absent current action rows are explicitly not promoted"
            ),
        },
        "checks": {
            "targetSnapshotComplete": True,
            "completePinnedPriorMapChainThroughV19": action_audit["priorMaps"] == 23,
            "presentV20OutputCompleteAndExact": (
                not V20_OUTPUT.exists() or action_audit["v20OutputComplete"]
            ),
            "presentV21OutputCompleteExactZeroRouteClosure": (
                not V21_OUTPUT.exists()
                or (
                    action_audit["v21OutputComplete"]
                    and action_audit["v21Signatures"] == 32
                    and action_audit["v21PriorMapsThroughV20"] == 24
                )
            ),
            "presentBatch1ProfileBackfillCompleteAndExact": (
                action_audit["profileBackfillBatch1OutputComplete"]
                and action_audit["profileBackfillBatch1Signatures"] == 32
            ),
            "presentBatch2ProfileBackfillCompleteAndExact": (
                action_audit["profileBackfillBatch2OutputComplete"]
                and action_audit["profileBackfillBatch2Signatures"] == 35
            ),
            "presentBatch3ProfileBackfillCompleteAndExact": (
                action_audit["profileBackfillBatch3OutputComplete"]
                and action_audit["profileBackfillBatch3Signatures"] == 36
            ),
            "presentBatch4ProfileBackfillCompleteAndExact": (
                action_audit["profileBackfillBatch4OutputComplete"]
                and action_audit["profileBackfillBatch4Signatures"] == 36
            ),
            "presentBatch5ProfileBackfillCompleteAndExact": (
                action_audit["profileBackfillBatch5OutputComplete"]
                and action_audit["profileBackfillBatch5Signatures"] == 32
            ),
            "presentBatch6ProfileBackfillCompleteAndExact": (
                action_audit["profileBackfillBatch6OutputComplete"]
                and action_audit["profileBackfillBatch6Signatures"] == 50
            ),
            "presentBatch7ProfileBackfillCompleteAndExact": (
                action_audit["profileBackfillBatch7OutputComplete"]
                and action_audit["profileBackfillBatch7Signatures"] == 50
                and action_audit["profileBackfillCombinedSignatures"] == 271
            ),
            "batch2PlanAcceptedAndTargetBoundaryMatchesCurrentLedger": True,
            "batch3PlanAcceptedAndTargetBoundaryMatchesCurrentLedger": True,
            "batch4PlanAcceptedAndTargetBoundaryMatchesCurrentLedger": True,
            "batch5PlanAcceptedAndTargetBoundaryMatchesCurrentLedger": True,
            "batch6PlanAcceptedAndTargetBoundaryMatchesCurrentLedger": True,
            "batch7PlanAcceptedAndTargetBoundaryMatchesCurrentLedger": True,
            "conditionalAutomaticExecutionPausedByCalibration": True,
            "allActionMapPinsMatch": True,
            "allOverlappingActionsAgree": action_audit["conflictingActionLabels"] == 0,
            "allPromotedSourcesAcceptedScoreable": True,
            "allAcceptedScoreableAnchorsAndSignaturesAudited": (
                coverage["acceptedScoreableAnchorRowsAudited"]
                == snapshot["acceptedAnchorRows"]
                and coverage["acceptedScoreableSignaturesWithAnchors"]
                == len(snapshot["owned"])
            ),
            "processedPairActionAnchorsExcluded": True,
            "allReceiptAndOutboxHashesPairsExcluded": True,
            "receiptOutboxBoundaryStableDuringAudit": True,
            "allFieldsAndPairsDeduplicated": True,
            "unresolvedProfilesNotPromoted": True,
            "coefficientAndCredentialPayloadOmitted": True,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "polynomialArithmeticRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "heavyWorkersLaunched": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if pair_audit.COEFFICIENT_LINE_RE.search(rendered):
        raise ValueError("coefficient payload entered re-intersection certificate")
    if not all(certificate["checks"].values()):
        raise ValueError("re-intersection checks are incomplete")
    atomic_replace(CERTIFICATE, rendered)

    summary = {
        "schemaVersion": "full-ledger-gold-reintersection-summary-v1",
        "status": certificate["status"],
        "certificate": artifact(CERTIFICATE),
        "acceptedScoreablePairs": len(snapshot["owned"]),
        "currentEligibleGoldPairs": len(snapshot["gold"]),
        "alreadyExactHits": len(exact["selected"]),
        "savedAllCompatibleExactOptionSets": len(saved_unresolved["allCompatible"]),
        "deterministicSingleOrbitRunbooks": len(deterministic),
        "allCompatibleSafeRoutes": len(all_safe),
        "conditionalRoutes": len(conditional),
        "conditionalDistinctTargets": len(public["targetIndex"]),
        "immediateFrontierExhausted": zero_immediate,
        "missingActionPairs": len(missing_action_pairs),
        "missingProfilePairs": coverage["signatureUnresolvedNoProfile"],
        "coefficientMaterialIncluded": False,
        "heavyWorkerLaunched": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_replace(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
