#!/usr/bin/env python3
"""Filter a campaign matrix by missing reports and/or prior campaign tasks."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--missing-report",
        action="append",
        type=Path,
        help="merge report containing missing_job_ids (repeatable)",
    )
    parser.add_argument(
        "--exclude-matrix",
        action="append",
        type=Path,
        default=[],
        help="matrix whose matching tasks should be excluded (repeatable)",
    )
    parser.add_argument(
        "--identity",
        choices=("job-id", "source-target", "source-hash-target"),
        default="source-hash-target",
        help="task identity used for --exclude-matrix",
    )
    args = parser.parse_args()

    if not args.missing_report and not args.exclude_matrix:
        parser.error("at least one --missing-report or --exclude-matrix is required")

    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    tasks = matrix.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("campaign matrix has no tasks")
    missing: set[str] = set()
    for report_path in args.missing_report or []:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        values = report.get("missing_job_ids")
        if not isinstance(values, list):
            raise ValueError(f"merge report has no missing_job_ids: {report_path}")
        missing.update(str(value) for value in values)
    available = {str(task.get("job_id")) for task in tasks}
    unknown = missing - available
    if unknown:
        raise ValueError(f"missing reports reference {len(unknown)} unknown job ids")
    selected = (
        [task for task in tasks if str(task.get("job_id")) in missing]
        if args.missing_report
        else list(tasks)
    )
    excluded: set[tuple[object, ...]] = set()
    for excluded_path in args.exclude_matrix:
        excluded_matrix = json.loads(excluded_path.read_text(encoding="utf-8"))
        excluded_tasks = excluded_matrix.get("tasks")
        if not isinstance(excluded_tasks, list):
            raise ValueError(f"campaign matrix has no tasks: {excluded_path}")
        excluded.update(_task_identity(task, args.identity) for task in excluded_tasks)
    if excluded:
        selected = [
            task for task in selected
            if _task_identity(task, args.identity) not in excluded
        ]
    filtered = dict(matrix)
    filtered["parent_matrix"] = str(args.matrix)
    filtered["filter_kind"] = "missing-and-excluded-tasks"
    filtered["missing_reports"] = [str(path) for path in args.missing_report or []]
    filtered["exclude_matrices"] = [str(path) for path in args.exclude_matrix]
    filtered["exclude_identity"] = args.identity
    filtered["tasks"] = [dict(task, index=index) for index, task in enumerate(selected)]
    filtered["task_count"] = len(selected)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=args.output.parent, delete=False
    ) as handle:
        json.dump(filtered, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(args.output)
    print(json.dumps({
        "source_tasks": len(tasks),
        "requested_missing_job_ids": len(missing),
        "excluded_task_identities": len(excluded),
        "selected_tasks": len(selected),
        "output": str(args.output),
    }, indent=2, sort_keys=True))


def _task_identity(task: dict[str, object], kind: str) -> tuple[object, ...]:
    if kind == "job-id":
        return (str(task.get("job_id") or ""),)
    target = int(task["target_t"])
    if kind == "source-target":
        return (int(task["source_t"]), target)
    if kind == "source-hash-target":
        return (str(task["source_candidate_hash"]), target)
    raise ValueError(f"unsupported task identity: {kind}")


if __name__ == "__main__":
    main()
