#!/usr/bin/env python3
"""Create a compact repair matrix from failed indices in a source campaign."""

from __future__ import annotations

import argparse
import json
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


def plan_repair(
    matrix_path: str | Path,
    failed_indices: Iterable[int],
    *,
    campaign_id: str,
    output_dir: str | Path,
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    project = Path(project).resolve()
    matrix_path = Path(matrix_path).resolve()
    output_dir = Path(output_dir).resolve()
    _require_within(matrix_path, project, "source matrix")
    _require_within(output_dir, project, "repair output")
    source = json.loads(matrix_path.read_text(encoding="utf-8"))
    source_tasks = source.get("tasks")
    if not isinstance(source_tasks, list) or not source_tasks:
        raise ValueError("source campaign matrix has no tasks")

    indices = sorted({int(value) for value in failed_indices})
    if not indices:
        raise ValueError("at least one failed task index is required")
    tasks: list[dict[str, Any]] = []
    for repair_index, source_index in enumerate(indices):
        if source_index < 0 or source_index >= len(source_tasks):
            raise IndexError(
                f"source task index {source_index} is outside [0, {len(source_tasks)})"
            )
        task = dict(source_tasks[source_index])
        manifest = (project / str(task["manifest"])).resolve()
        _require_within(manifest, project, "task manifest")
        if _sha256(manifest) != str(task.get("manifest_sha256") or ""):
            raise ValueError(f"source manifest changed: {task.get('job_id')}")
        task["index"] = repair_index
        task["source_index"] = source_index
        tasks.append(task)

    campaign = _safe_id(campaign_id)
    repair = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "source_campaign_id": source.get("campaign_id"),
        "source_matrix": _relative(matrix_path, project),
        "target_snapshot_id": source.get("target_snapshot_id"),
        "inputs": source.get("inputs", []),
        "task_count": len(tasks),
        "source_task_count": len(source_tasks),
        "repair_indices": indices,
        "tasks": tasks,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "matrix.json"
    _atomic_json(output_path, repair)
    summary = {
        "campaign_id": campaign,
        "source_campaign_id": source.get("campaign_id"),
        "matrix": _relative(output_path, project),
        "task_count": len(tasks),
        "repair_indices": indices,
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument(
        "--indices",
        required=True,
        help="comma-separated source task indices",
    )
    args = parser.parse_args()
    indices = [int(value) for value in args.indices.split(",") if value.strip()]
    summary = plan_repair(
        args.matrix,
        indices,
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
