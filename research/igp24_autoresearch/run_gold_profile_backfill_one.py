#!/usr/bin/env python3
"""Fail-closed resumable coordinator for one exact profile-backfill worker.

Without ``--execute`` this is a read-only plan audit.  Execution acquires the
shared heavy-worker lock, revalidates every pinned boundary, and launches at
most one allowlisted Sage process synchronously.  Exact checkpoint rows are
retained across interruption; later invocations compute only missing labels.
There is no network or submission code.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import audit_full_ledger_gold_reintersection as audit
import prepare_v11_pair_delta as helper


ROOT = audit.ROOT
DEFAULT_PLAN = audit.DATA / "gold_profile_backfill_20260722_batch1/provenance_plan.json"


class GuardFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def resolve(value: object) -> Path:
    path = Path(str(value)).expanduser()
    path = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    if not path.is_relative_to(ROOT):
        raise GuardFailure(f"path escapes project root: {path}")
    return path


def read_json(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if audit.pair_audit.COEFFICIENT_LINE_RE.search(raw):
        raise GuardFailure(f"coefficient payload in coordinator metadata: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise GuardFailure(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise GuardFailure(f"non-object worker row at {path}:{line_number}")
        rows.append(value)
    return rows


def atomic_rows(path: Path, rows: list[dict]) -> None:
    payload = helper.canonical_jsonl(rows)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def pinned_artifact(item: dict) -> Path:
    path = resolve(item.get("path"))
    if not path.is_file() or helper.sha256_path(path) != str(item.get("sha256")):
        raise GuardFailure(f"pinned artifact changed: {path}")
    return path


def validate_boundary(plan: dict) -> None:
    connection = sqlite3.connect(f"file:{audit.DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        snapshot = audit.ledger_snapshot(connection)
    finally:
        connection.close()
    boundary = plan["boundary"]
    if (
        len(snapshot["owned"]) != int(boundary["acceptedScoreablePairs"])
        or snapshot["acceptedAnchorRows"]
        != int(boundary["acceptedScoreableAnchorRows"])
        or snapshot["acceptedPairSetSha256"] != boundary["acceptedPairSetSha256"]
        or len(snapshot["targets"]) != int(boundary["targetRows"])
        or snapshot["targetSnapshotSha256"] != boundary["targetSnapshotSha256"]
        or audit.compact_boundary(audit.volatile_boundary())
        != boundary["receiptAndOutboxBoundary"]
    ):
        raise GuardFailure("accepted, target, receipt, or outbox boundary changed")


def validate_plan(path: Path) -> tuple[dict, dict]:
    plan = read_json(path)
    if (
        plan.get("schemaVersion") != "gold-profile-backfill-one-worker-plan-v1"
        or plan.get("status") != "ready_waiting_for_root_heavy_clearance"
        or plan.get("coefficientMaterialIncluded") is not False
        or plan.get("credentialMaterialIncluded") is not False
        or (plan.get("execution") or {}).get("heavyWorkerLaunched") is not False
        or (plan.get("execution") or {}).get("submissionAuthorized") is not False
    ):
        raise GuardFailure("profile plan is not a finalized light-only handoff")
    artifacts = plan["artifacts"]
    for key in (
        "fullGoldAudit",
        "ranking",
        "input",
        "emptyPriorInput",
        "worker",
        "coordinator",
    ):
        pinned_artifact(artifacts[key])
    for item in artifacts["actionMaps"]:
        pinned_artifact(item)
    for item in artifacts["profileArtifactsAtSeal"]:
        pinned_artifact(item)
    ranking = read_json(resolve(artifacts["ranking"]["path"]))
    selected = [str(value) for value in plan.get("selectedSignatures") or []]
    if (
        len(selected) != len(set(selected))
        or len(selected) != int(plan["selection"]["selectedProfiles"])
        or selected
        != [str(row["sourcePair"]) for row in ranking["selectedProfileRows"]]
    ):
        raise GuardFailure("selected profile signature list changed")
    argv = list(plan["execution"].get("heavyWorkerArgv") or [])
    if (
        argv[:3]
        != [
            "/usr/local/bin/sage",
            "-python",
            "agent_index24_missing_pair_census.sage.py",
        ]
        or "--signature-aware" not in argv
        or argv[argv.index("--checkpoint-every") + 1] != "1"
        or argv[argv.index("--shard-count") + 1] != "1"
    ):
        raise GuardFailure("worker argv is not the allowlisted exact one-worker census")
    validate_boundary(plan)
    return plan, ranking


def selected_by_label(plan: dict) -> dict[str, set[int]]:
    result = {}
    for value in plan["selectedSignatures"]:
        label, raw_r = str(value).rsplit("/r", 1)
        result.setdefault(label, set()).add(int(raw_r))
    return result


def validate_worker_rows(rows: list[dict], plan: dict) -> dict[str, dict]:
    selected = selected_by_label(plan)
    result = {}
    for row in rows:
        label = str(row.get("sourceLabel"))
        values = {int(value) for value in row.get("sourceR") or []}
        if (
            label not in selected
            or values != selected[label]
            or label in result
            or row.get("status") != "certified"
            or not helper.certificate_is_exact(row)
        ):
            raise GuardFailure(f"invalid or duplicate checkpoint row for {label}")
        result[label] = row
    return result


def merge_checkpoint(plan: dict) -> tuple[dict[str, dict], Path, Path]:
    artifacts = plan["artifacts"]
    output = resolve(artifacts["output"])
    chunk = resolve(artifacts["resumeChunk"])
    main_rows = validate_worker_rows(read_jsonl(output), plan)
    chunk_rows = validate_worker_rows(read_jsonl(chunk), plan)
    overlap = set(main_rows) & set(chunk_rows)
    if overlap:
        for label in overlap:
            if audit.pair_audit.action_core(main_rows[label]) != audit.pair_audit.action_core(
                chunk_rows[label]
            ):
                raise GuardFailure(f"conflicting resume checkpoint row: {label}")
    combined = {**main_rows, **chunk_rows}
    if chunk_rows:
        order = list(selected_by_label(plan))
        atomic_rows(output, [combined[label] for label in order if label in combined])
        chunk.unlink()
    return combined, output, chunk


def resume_input(plan: dict, completed: set[str]) -> Path:
    original = resolve(plan["artifacts"]["input"]["path"])
    destination = resolve(plan["artifacts"]["resumeInput"])
    rows = read_jsonl(original)
    for row in rows:
        if str(row["label"]) in completed:
            row["isOwnedSource"] = False
            row["sourceR"] = []
    expected_remaining = set(selected_by_label(plan)) - completed
    actual_remaining = {
        str(row["label"]) for row in rows if row.get("isOwnedSource")
    }
    if actual_remaining != expected_remaining:
        raise GuardFailure("runtime resume input does not isolate remaining labels")
    helper.atomic_text(destination, helper.canonical_jsonl(rows))
    return destination


def replace_argv_value(argv: list[str], option: str, value: Path) -> None:
    try:
        argv[argv.index(option) + 1] = str(value.relative_to(ROOT))
    except (ValueError, IndexError) as exc:
        raise GuardFailure(f"worker argv lacks {option}") from exc


def execute(plan_path: Path) -> dict:
    plan, _ranking = validate_plan(plan_path)
    lock = resolve(plan["artifacts"]["lock"])
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GuardFailure("shared Sage/GAP heavy-worker lock is busy") from exc

        # Revalidate after the lease is held; no boundary may drift between
        # audit and worker start.
        plan, _ranking = validate_plan(plan_path)
        completed, output, chunk = merge_checkpoint(plan)
        selected_labels = set(selected_by_label(plan))
        remaining = selected_labels - set(completed)
        if not remaining:
            return {
                "status": "complete_existing_exact_checkpoint",
                "completedLabels": len(completed),
                "remainingLabels": 0,
                "workerLaunchedThisRun": False,
            }

        argv = list(plan["execution"]["heavyWorkerArgv"])
        if completed:
            runtime_input = resume_input(plan, set(completed))
            replace_argv_value(argv, "--input", runtime_input)
            replace_argv_value(argv, "--output", chunk)
            if chunk.exists():
                raise GuardFailure("resume chunk unexpectedly exists after checkpoint merge")
        elif output.exists():
            raise GuardFailure("empty/noncanonical main checkpoint exists")

        result = subprocess.run(argv, cwd=ROOT, check=False)
        if result.returncode != 0:
            raise GuardFailure(
                f"exact profile worker exited {result.returncode}; checkpoint retained"
            )
        completed, _output, _chunk = merge_checkpoint(plan)
        missing = selected_labels - set(completed)
        if missing:
            raise GuardFailure(
                f"worker returned success but {len(missing)} selected labels remain"
            )
        return {
            "status": "complete_exact_profiles",
            "completedLabels": len(completed),
            "remainingLabels": 0,
            "workerLaunchedThisRun": True,
        }


def audit_only(plan_path: Path) -> dict:
    plan, _ranking = validate_plan(plan_path)
    output = resolve(plan["artifacts"]["output"])
    chunk = resolve(plan["artifacts"]["resumeChunk"])
    rows = {
        **validate_worker_rows(read_jsonl(output), plan),
        **validate_worker_rows(read_jsonl(chunk), plan),
    }
    selected = set(selected_by_label(plan))
    return {
        "status": "ready" if not rows else "checkpoint_present",
        "selectedProfiles": int(plan["selection"]["selectedProfiles"]),
        "selectedLabels": len(selected),
        "completedLabels": len(rows),
        "remainingLabels": len(selected - set(rows)),
        "workerLaunched": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }


def main() -> int:
    args = parse_args()
    plan_path = resolve(args.plan)
    result = execute(plan_path) if args.execute else audit_only(plan_path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GuardFailure, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
