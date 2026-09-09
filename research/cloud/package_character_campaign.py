#!/usr/bin/env python3
"""Create a deterministic, secret-free source bundle for a campaign matrix."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Iterable

from cloud.generator_worker import PROJECT


FORBIDDEN_PARTS = {
    ".git",
    "igp24_config.py",
    "submitted_hashes.txt",
    "control.sqlite3",
    "ledger.sqlite3",
    "api_key",
}


def package_campaign(
    matrix_path: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    matrix_path = _project_path(matrix_path, "matrix")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    tasks = matrix.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("campaign matrix has no tasks")
    files: set[Path] = {matrix_path}
    files.update(path for path in (PROJECT / "cloud").glob("*.py") if path.is_file())
    files.update(path for path in (PROJECT / "routeA").glob("**/*.py") if path.is_file())
    for input_row in matrix.get("inputs", []):
        path = _project_path(input_row["path"], "input")
        if _sha256(path) != input_row.get("sha256"):
            raise ValueError(f"campaign input changed before packaging: {path}")
        files.add(path)
    for task in tasks:
        manifest = _project_path(task["manifest"], "manifest")
        if _sha256(manifest) != task.get("manifest_sha256"):
            raise ValueError(f"campaign manifest changed before packaging: {manifest}")
        files.add(manifest)
        manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
        for input_row in manifest_value.get("inputs", []):
            path = _project_path(input_row["path"], "input")
            if _sha256(path) != input_row.get("sha256"):
                raise ValueError(f"task input changed before packaging: {path}")
            files.add(path)
    for path in files:
        _assert_secret_free(path)

    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_handle,
            mtime=0,
        ) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for path in sorted(files):
                    _add_deterministic(archive, path)
    temporary.replace(destination)
    summary = {
        "campaign_id": matrix.get("campaign_id"),
        "matrix": str(matrix_path.relative_to(PROJECT)),
        "task_count": len(tasks),
        "file_count": len(files),
        "package": str(destination),
        "package_size": destination.stat().st_size,
        "package_sha256": _sha256(destination),
        "contains_submission_credentials": False,
    }
    report_path = destination.with_suffix(destination.suffix + ".report.json")
    _atomic_json(report_path, summary)
    return summary


def _add_deterministic(archive: tarfile.TarFile, path: Path) -> None:
    info = archive.gettarinfo(str(path), arcname=str(path.relative_to(PROJECT)))
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
    with path.open("rb") as handle:
        archive.addfile(info, handle)


def _assert_secret_free(path: Path) -> None:
    lowered = {part.lower() for part in path.parts}
    forbidden = {value.lower() for value in FORBIDDEN_PARTS}
    if lowered & forbidden or path.suffix in {".sqlite", ".sqlite3", ".db"}:
        raise ValueError(f"credential/state file cannot enter cloud package: {path}")


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


def _atomic_json(path: Path, value: Any) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = handle.name
    Path(temporary).replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    summary = package_campaign(args.matrix, args.destination)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
