#!/usr/bin/env python3
"""Verify and merge local or archived character-discovery array results."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from cloud.generator_worker import PROJECT


def merge_campaign(
    matrix_path: str | Path,
    output_path: str | Path,
    *,
    archive_dir: str | Path | None = None,
    allow_incomplete: bool = False,
    artifact_kind: str = "discoveries",
) -> dict[str, Any]:
    matrix_path = _project_path(matrix_path, "matrix")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    tasks = matrix.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("campaign matrix has no tasks list")
    archived = _load_archives(Path(archive_dir)) if archive_dir else {}
    if artifact_kind not in {"discoveries", "candidates"}:
        raise ValueError("artifact_kind must be discoveries or candidates")
    artifact_suffix = f".{artifact_kind}.jsonl"
    by_row: dict[str, dict[str, Any]] = {}
    raw_rows = 0
    completed = 0
    missing: list[str] = []
    lane_counts: Counter[str] = Counter()

    for task in tasks:
        job_id = str(task["job_id"])
        manifest_path = _project_path(task["manifest"], "manifest")
        expected_manifest_hash = str(task["manifest_sha256"])
        if _sha256(manifest_path) != expected_manifest_hash:
            raise ValueError(f"local manifest changed after planning: {job_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report_path = str(manifest.get("report") or f"cloud/output/{job_id}.report.json")
        if archived:
            files = archived.get(job_id)
            if files is None:
                missing.append(job_id)
                continue
            raw_report = files.get(report_path)
            if raw_report is None:
                raise ValueError(f"archive for {job_id} has no declared report")
            report = json.loads(raw_report)
            artifact_reader = lambda path, files=files: files.get(path)
        else:
            local_report = _project_path(report_path, "report")
            if not local_report.exists():
                missing.append(job_id)
                continue
            report = json.loads(local_report.read_text(encoding="utf-8"))

            def artifact_reader(path: str) -> bytes | None:
                local = _project_path(path, "artifact")
                return local.read_bytes() if local.exists() else None

        _validate_report(report, task)
        completed += 1
        lane = str(task["lane"])
        lane_counts[lane] += 1
        for artifact in report.get("artifacts", []):
            if not artifact.get("exists"):
                continue
            path = str(artifact["path"])
            payload = artifact_reader(path)
            if payload is None:
                raise ValueError(f"result for {job_id} is missing artifact {path}")
            if hashlib.sha256(payload).hexdigest() != artifact.get("sha256"):
                raise ValueError(f"artifact checksum mismatch for {path}")
            if not path.endswith(artifact_suffix):
                continue
            for number, raw_line in enumerate(payload.decode("utf-8").splitlines(), 1):
                if not raw_line.strip():
                    continue
                try:
                    row = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in {path}:{number}") from exc
                raw_rows += 1
                enriched = dict(row)
                enriched["cloud_campaign_id"] = matrix.get("campaign_id")
                enriched["cloud_job_id"] = job_id
                enriched["cloud_lane"] = lane
                enriched["cloud_manifest_sha256"] = task["manifest_sha256"]
                key = (
                    _seed_key(enriched)
                    if artifact_kind == "discoveries"
                    else _candidate_key(enriched)
                )
                previous = by_row.get(key)
                if previous is None or _row_quality(enriched) > _row_quality(previous):
                    by_row[key] = enriched

    if missing and not allow_incomplete:
        raise RuntimeError(
            f"campaign has {len(missing)} missing tasks; "
            "use --allow-incomplete only for an intentional partial merge"
        )
    rows = sorted(by_row.values(), key=lambda row: _row_sort_key(row, artifact_kind))
    output_path = Path(output_path)
    _atomic_text(
        output_path,
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
    )
    summary = {
        "campaign_id": matrix.get("campaign_id"),
        "task_count": len(tasks),
        "completed_tasks": completed,
        "missing_tasks": len(missing),
        "missing_job_ids": missing,
        "lane_completed": dict(sorted(lane_counts.items())),
        "artifact_kind": artifact_kind,
        "raw_rows": raw_rows,
        "unique_rows": len(rows),
        "base_count": len({
            base_t
            for row in rows
            if (base_t := _row_base_t(row)) is not None
        }),
        "target_count": len({
            int(row.get("starting_target_t", row.get("target_t")))
            for row in rows
        }),
        "output": str(output_path),
        "output_sha256": _sha256(output_path),
    }
    _atomic_text(
        output_path.with_suffix(output_path.suffix + ".report.json"),
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    return summary


def _candidate_key(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("candidate_hash") or "")
    if explicit:
        return explicit
    coefficients = str(row.get("coefficients") or "")
    if not coefficients:
        raise ValueError("candidate row has no hash or coefficients")
    return hashlib.sha256(coefficients.encode("ascii")).hexdigest()


def _row_base_t(row: Mapping[str, Any]) -> int | None:
    value = row.get("base_t")
    if value is None and isinstance(row.get("parameters"), Mapping):
        value = row["parameters"].get("base_t")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _row_sort_key(row: Mapping[str, Any], artifact_kind: str) -> tuple[Any, ...]:
    if artifact_kind == "candidates":
        return (
            int(row.get("target_t", -1)),
            int(row.get("target_r", -1)),
            int(row.get("field_disc_abs") or row.get("estimated_nfdisc_abs") or 0),
            _candidate_key(row),
        )
    return (
        int(row.get("base_t", -1)),
        int(row.get("starting_target_t", -1)),
        int(row.get("norm_squareclass", 0)),
        _seed_key(row),
    )


def _load_archives(directory: Path) -> dict[str, dict[str, bytes]]:
    if not directory.exists():
        raise FileNotFoundError(directory)
    by_job: dict[str, dict[str, bytes]] = {}
    for archive_path in sorted(directory.glob("*.tar.gz")):
        files: dict[str, bytes] = {}
        total_size = 0
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                pure = PurePosixPath(member.name)
                if (
                    member.issym()
                    or member.islnk()
                    or member.isdir()
                    or pure.is_absolute()
                    or ".." in pure.parts
                ):
                    if member.isdir():
                        continue
                    raise ValueError(f"unsafe archive member in {archive_path}: {member.name}")
                total_size += int(member.size)
                if total_size > 100 * 1024 * 1024:
                    raise ValueError(f"result archive is too large: {archive_path}")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError(f"cannot read {member.name} from {archive_path}")
                files[str(pure)] = handle.read()
        reports = []
        for name, payload in files.items():
            if not name.endswith(".report.json"):
                continue
            try:
                candidate = json.loads(payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            # Current generator workers persist ``*.report.json``.  Older
            # campaigns used ``*.worker.report.json``.  Identify the report
            # from its required schema so both archive formats remain valid
            # without mistaking an artifact-side report for a worker report.
            if (
                isinstance(candidate, dict)
                and candidate.get("job_id")
                and candidate.get("manifest_sha256")
                and "returncode" in candidate
                and isinstance(candidate.get("artifacts"), list)
            ):
                reports.append(candidate)
        if len(reports) != 1:
            raise ValueError(
                f"result archive {archive_path} must contain exactly one worker report"
            )
        job_id = str(reports[0].get("job_id") or "")
        if not job_id or job_id in by_job:
            raise ValueError(f"duplicate or missing job id in {archive_path}")
        by_job[job_id] = files
    return by_job


def _validate_report(report: Mapping[str, Any], task: Mapping[str, Any]) -> None:
    if report.get("job_id") != task.get("job_id"):
        raise ValueError("worker report job id does not match matrix")
    if report.get("manifest_sha256") != task.get("manifest_sha256"):
        raise ValueError(f"worker report used a different manifest: {task.get('job_id')}")
    if int(report.get("returncode", -1)) != 0:
        raise ValueError(f"worker task failed: {task.get('job_id')}")
    if report.get("artifact_errors"):
        raise ValueError(f"worker task has artifact errors: {task.get('job_id')}")


def _seed_key(row: Mapping[str, Any]) -> str:
    seed = row.get("seed_coefficients")
    if seed is None:
        seed = ["linear", int(row.get("shift", 0))]
    payload = {
        "label": str(row.get("label")),
        "base_t": int(row.get("base_t", -1)),
        "norm_squareclass": int(row.get("norm_squareclass", 0)),
        "seed_coefficients": [str(value) for value in seed],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _row_quality(row: Mapping[str, Any]) -> tuple[int, int, int]:
    return (
        int(bool(row.get("calibration_kind"))),
        int(bool(row.get("pilot_terminal_t"))),
        -len(json.dumps(row, sort_keys=True)),
    )


def _project_path(raw: str | Path, kind: str) -> Path:
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


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        temporary = handle.name
    Path(temporary).replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument(
        "--artifact-kind",
        choices=("discoveries", "candidates"),
        default="discoveries",
    )
    args = parser.parse_args()
    summary = merge_campaign(
        args.matrix,
        args.output,
        archive_dir=args.archive_dir,
        allow_incomplete=args.allow_incomplete,
        artifact_kind=args.artifact_kind,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
