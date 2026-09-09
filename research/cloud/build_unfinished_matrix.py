#!/usr/bin/env python3
"""Mechanically reindex the highest-priority unfinished array tasks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("matrix", type=Path)
    parser.add_argument("archive_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--exclude-candidates", action="append", type=Path, default=[])
    parser.add_argument("--exclude-matrix", action="append", type=Path, default=[])
    parser.add_argument("--missing-report", type=Path)
    parser.add_argument(
        "--sort-opportunity",
        action="store_true",
        help="select unfinished tasks by descending live opportunity",
    )
    args = parser.parse_args()

    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    completed = {
        int(path.name.removeprefix("task-").removesuffix(".tar.gz"))
        for path in args.archive_dir.glob("task-*.tar.gz")
    }
    missing_job_ids: set[str] | None = None
    if args.missing_report is not None:
        report = json.loads(args.missing_report.read_text(encoding="utf-8"))
        missing_job_ids = {str(value) for value in report.get("missing_job_ids", [])}
    excluded_combos: set[tuple[str, int]] = set()
    excluded_job_ids: set[str] = set()
    for path in args.exclude_candidates:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            parameters = row.get("parameters") or {}
            source_hash = str(parameters.get("source_candidate_hash") or "")
            target_t = row.get("target_t", parameters.get("target_t"))
            if source_hash and target_t is not None:
                excluded_combos.add((source_hash, int(target_t)))
    for path in args.exclude_matrix:
        other = json.loads(path.read_text(encoding="utf-8"))
        for task in other.get("tasks", []):
            if task.get("job_id") is not None:
                excluded_job_ids.add(str(task["job_id"]))
            source_hash = str(task.get("source_candidate_hash") or "")
            target_ts = task.get("target_ts")
            if target_ts is None:
                target_t = task.get("target_t")
                target_ts = [] if target_t is None else [target_t]
            if source_hash:
                excluded_combos.update((source_hash, int(target_t)) for target_t in target_ts)

    def task_is_excluded(task: dict) -> bool:
        if str(task.get("job_id")) in excluded_job_ids:
            return True
        source_hash = str(task.get("source_candidate_hash") or "")
        target_ts = task.get("target_ts")
        if target_ts is None:
            target_t = task.get("target_t")
            target_ts = [] if target_t is None else [target_t]
        return any((source_hash, int(target_t)) in excluded_combos for target_t in target_ts)

    tasks = [
        task for task in matrix["tasks"]
        if int(task["index"]) not in completed
        and (missing_job_ids is None or str(task.get("job_id")) in missing_job_ids)
        and not task_is_excluded(task)
    ]
    if args.sort_opportunity:
        tasks.sort(key=lambda task: float(task.get("live_opportunity") or 0), reverse=True)
    tasks = tasks[: args.limit]
    for index, task in enumerate(tasks):
        task["index"] = index
    matrix["tasks"] = tasks
    matrix["task_count"] = len(tasks)
    args.output.write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "completed_excluded": len(completed),
        "source_target_combos_excluded": len(excluded_combos),
        "job_ids_excluded": len(excluded_job_ids),
        "selected": len(tasks),
        "selected_opportunity": sum(float(task["live_opportunity"]) for task in tasks),
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
