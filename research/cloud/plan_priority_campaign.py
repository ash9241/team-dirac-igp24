#!/usr/bin/env python3
"""Reorder one or more exact campaign matrices for score-first execution.

The planner preserves the original, checksummed task manifests.  It merely
deduplicates tasks and interleaves alternatives so the first scheduling wave
tries one candidate for as many high-value target groups as possible before
spending cores on second or later alternatives.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from cloud.plan_character_campaign import (
    PROJECT,
    _atomic_json,
    _now,
    _relative,
    _require_within,
    _safe_id,
    _sha256,
)


def plan_priority_campaign(
    matrix_paths: Iterable[str | Path],
    *,
    campaign_id: str,
    output_dir: str | Path,
    project: str | Path = PROJECT,
    exclude_job_ids: Iterable[str] = (),
) -> dict[str, Any]:
    project = Path(project).resolve()
    output_dir = Path(output_dir).resolve()
    _require_within(output_dir, project, "priority output")
    paths = [Path(path).resolve() for path in matrix_paths]
    if not paths:
        raise ValueError("at least one source matrix is required")

    excluded = {str(value) for value in exclude_job_ids}
    source_rows: list[dict[str, Any]] = []
    input_by_path: dict[str, dict[str, Any]] = {}
    task_by_job: dict[str, dict[str, Any]] = {}
    duplicate_tasks = 0
    for path in paths:
        _require_within(path, project, "source matrix")
        source = json.loads(path.read_text(encoding="utf-8"))
        tasks = source.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError(f"source matrix has no tasks: {path}")
        source_rows.append({
            "campaign_id": source.get("campaign_id"),
            "matrix": _relative(path, project),
            "task_count": len(tasks),
        })
        for raw_input in source.get("inputs", []):
            key = str(raw_input["path"])
            previous = input_by_path.get(key)
            if previous is not None and previous.get("sha256") != raw_input.get("sha256"):
                raise ValueError(f"conflicting input checksum for {key}")
            input_by_path[key] = dict(raw_input)
        for raw_task in tasks:
            task = dict(raw_task)
            job_id = str(task.get("job_id") or "")
            if not job_id:
                raise ValueError(f"task in {path} has no job_id")
            if job_id in excluded:
                continue
            manifest = (project / str(task["manifest"])).resolve()
            _require_within(manifest, project, "task manifest")
            if _sha256(manifest) != str(task.get("manifest_sha256") or ""):
                raise ValueError(f"source manifest changed: {job_id}")
            previous = task_by_job.get(job_id)
            if previous is not None:
                duplicate_tasks += 1
                if (
                    previous.get("manifest") != task.get("manifest")
                    or previous.get("manifest_sha256") != task.get("manifest_sha256")
                ):
                    raise ValueError(f"conflicting duplicate task: {job_id}")
                continue
            task_by_job[job_id] = task

    by_target: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for task in task_by_job.values():
        try:
            target_t = int(task["target_t"])
            opportunity = float(task.get("live_opportunity") or 0.0)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"task lacks target/opportunity: {task.get('job_id')}") from exc
        if opportunity < 0:
            raise ValueError(f"negative live opportunity: {task.get('job_id')}")
        task["target_t"] = target_t
        task["live_opportunity"] = opportunity
        by_target[target_t].append(task)

    for alternatives in by_target.values():
        alternatives.sort(key=lambda row: (
            _source_priority(row),
            str(row.get("source_candidate_hash") or ""),
            str(row["job_id"]),
        ))
    target_order = sorted(
        by_target,
        key=lambda target_t: (
            -max(float(row["live_opportunity"]) for row in by_target[target_t]),
            target_t,
        ),
    )
    ordered: list[dict[str, Any]] = []
    maximum_alternatives = max(map(len, by_target.values()), default=0)
    for alternative_index in range(maximum_alternatives):
        for target_t in target_order:
            alternatives = by_target[target_t]
            if alternative_index >= len(alternatives):
                continue
            task = dict(alternatives[alternative_index])
            task["priority_alternative_index"] = alternative_index
            ordered.append(task)
    for index, task in enumerate(ordered):
        task["index"] = index

    if not ordered:
        raise ValueError("no tasks remain after exclusions")
    campaign = _safe_id(campaign_id)
    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "source_matrices": source_rows,
        "inputs": [input_by_path[key] for key in sorted(input_by_path)],
        "task_count": len(ordered),
        "priority_kind": "target-breadth-then-live-opportunity",
        "tasks": ordered,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "matrix.json"
    _atomic_json(output_path, matrix)
    target_ceiling = sum(
        max(float(row["live_opportunity"]) for row in alternatives)
        for alternatives in by_target.values()
    )
    first_wave = ordered[: min(400, len(ordered))]
    summary = {
        "campaign_id": campaign,
        "matrix": _relative(output_path, project),
        "task_count": len(ordered),
        "target_count": len(by_target),
        "target_ceiling": target_ceiling,
        "excluded_job_ids": len(excluded),
        "duplicate_source_tasks": duplicate_tasks,
        "first_400_tasks": len(first_wave),
        "first_400_targets": len({int(row["target_t"]) for row in first_wave}),
        "first_400_target_ceiling": sum(
            max(float(row["live_opportunity"]) for row in first_wave if int(row["target_t"]) == target_t)
            for target_t in {int(row["target_t"]) for row in first_wave}
        ),
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def _source_priority(task: dict[str, Any]) -> int:
    """Preserve a source planner's lower-discriminant ordering when present."""

    try:
        return int(task["source_index"])
    except (KeyError, TypeError, ValueError):
        return 10**18


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("matrices", nargs="+", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--exclude-job-id", action="append", default=[])
    args = parser.parse_args()
    summary = plan_priority_campaign(
        args.matrices,
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
        exclude_job_ids=args.exclude_job_id,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
