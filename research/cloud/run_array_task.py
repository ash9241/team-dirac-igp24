#!/usr/bin/env python3
"""Execute one checksummed campaign-matrix task by array index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path
from typing import Any

from cloud.generator_worker import PROJECT, run_job


def run_array_task(
    matrix_path: str | Path,
    task_index: int,
    *,
    result_archive: str | Path | None = None,
) -> dict[str, Any]:
    matrix_path = _project_path(matrix_path, "matrix")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    tasks = matrix.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("campaign matrix has no tasks list")
    index = int(task_index)
    if index < 0 or index >= len(tasks):
        raise IndexError(f"task index {index} is outside [0, {len(tasks)})")
    task = tasks[index]
    if int(task.get("index", -1)) != index:
        raise ValueError(f"matrix task {index} has inconsistent index metadata")
    manifest_path = _project_path(task.get("manifest"), "manifest")
    expected = str(task.get("manifest_sha256") or "").lower()
    actual = _sha256(manifest_path)
    if expected != actual:
        raise ValueError(
            f"manifest checksum mismatch for task {index}: "
            f"expected {expected}, got {actual}"
        )
    report = run_job(manifest_path)
    if report.get("job_id") != task.get("job_id"):
        raise ValueError("worker report job id does not match the task matrix")
    if result_archive is not None:
        _write_result_archive(report, Path(result_archive))
    return {
        "campaign_id": matrix.get("campaign_id"),
        "task_index": index,
        "task_count": len(tasks),
        "job_id": report["job_id"],
        "returncode": report["returncode"],
        "artifacts": report["artifacts"],
        "result_archive": str(result_archive) if result_archive else None,
    }


def _write_result_archive(report: dict[str, Any], destination: Path) -> None:
    report_path = _project_path(report.get("report_path"), "report")
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    if persisted.get("manifest_sha256") != report.get("manifest_sha256"):
        raise ValueError("persisted report does not match the completed task")
    paths = [report_path]
    for artifact in report.get("artifacts", []):
        if not artifact.get("exists"):
            continue
        path = _project_path(artifact["path"], "artifact")
        if _sha256(path) != artifact.get("sha256"):
            raise ValueError(f"artifact changed before archive: {artifact['path']}")
        paths.append(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with tarfile.open(temporary, "w:gz") as archive:
        for path in sorted(set(paths)):
            archive.add(path, arcname=str(path.relative_to(PROJECT)), recursive=False)
    temporary.replace(destination)


def _project_path(raw: Any, kind: str) -> Path:
    if raw is None:
        raise ValueError(f"{kind} path is missing")
    path = (PROJECT / str(raw)).resolve()
    if path != PROJECT and PROJECT not in path.parents:
        raise ValueError(f"{kind} escapes project root: {raw}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--task-index", type=int)
    parser.add_argument("--result-archive", type=Path)
    args = parser.parse_args()
    raw_index = args.task_index
    if raw_index is None:
        value = os.environ.get("BATCH_TASK_INDEX")
        if value is None:
            parser.error("--task-index or BATCH_TASK_INDEX is required")
        raw_index = int(value)
    result = run_array_task(
        args.matrix,
        raw_index,
        result_archive=args.result_archive,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
