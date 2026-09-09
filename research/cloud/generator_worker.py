#!/usr/bin/env python3
"""Run an allow-listed, credential-free generation job on GCP.

Example manifest:
{
  "job_id": "product-map-001",
  "module": "routeA.gap_product_map",
  "args": ["routeA/data/components/octics.jsonl", "routeA/data/components/cubics.jsonl",
           "--output", "cloud/output/product_map.jsonl"],
  "artifacts": ["cloud/output/product_map.jsonl"]
}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parent.parent
ALLOWED_MODULES = {
    "routeA.build_discovered_character_campaign",
    "routeA.discover_character_closure",
    "routeA.discover_kernel_product_characters",
    "routeA.discover_linear_characters",
    "routeA.discover_norm_equation_characters",
    "routeA.discover_product_characters",
    "routeA.generate_degree24_pair_resolvents",
    "routeA.build_component_library",
    "routeA.gap_product_map",
    "routeA.gap_degree_product_map",
    "routeA.gap_block_census",
    "routeA.gap_pair_product_atlas",
    "routeA.gap_ordered_affine_atlas",
    "routeA.gap_features",
    "routeA.build_8x3_candidates",
    "routeA.build_wreath_seed_campaign",
    "routeA.build_subfield_kummer_campaign",
    "routeA.build_subfield_product_campaign",
    "routeA.build_subfield_unit_twist_campaign",
    "routeA.build_tower_experiment",
    "routeA.build_empirical_tower_experiment",
}


def run_job(manifest_path: Path) -> dict[str, Any]:
    if os.environ.get("IGP24_API_KEY") or os.environ.get("IGP24_API_KEY_FILE"):
        raise RuntimeError("cloud generation workers must not receive submission credentials")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    module = str(manifest.get("module", ""))
    if module not in ALLOWED_MODULES:
        raise ValueError(f"module is not allow-listed: {module}")
    job_id = str(manifest.get("job_id") or "")
    if not job_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in job_id):
        raise ValueError("job_id must contain only letters, numbers, hyphen, and underscore")
    args = [str(value) for value in manifest.get("args", [])]
    gp_override = os.environ.get("IGP24_GP")
    if gp_override:
        for index, value in enumerate(args[:-1]):
            if value == "--gp":
                args[index + 1] = gp_override
    inputs = _validate_inputs(manifest.get("inputs", []))
    for raw_spec in manifest.get("artifacts", []):
        spec = {"path": raw_spec} if isinstance(raw_spec, str) else dict(raw_spec)
        raw_path = spec.get("path")
        if not raw_path:
            raise ValueError("artifact entry has no path")
        _project_path(raw_path, kind="artifact").parent.mkdir(
            parents=True, exist_ok=True
        )
    _project_path(
        manifest.get("report") or f"cloud/output/{job_id}.report.json",
        kind="report",
    ).parent.mkdir(parents=True, exist_ok=True)
    started = _now()
    environment = os.environ.copy()
    environment.pop("IGP24_API_KEY", None)
    environment.pop("IGP24_API_KEY_FILE", None)
    python_executable = _python_executable(environment)
    result = subprocess.run(
        [python_executable, "-m", module, *args],
        cwd=PROJECT,
        env=environment,
        capture_output=True,
        text=True,
    )
    artifacts = []
    artifact_errors = []
    for raw_spec in manifest.get("artifacts", []):
        spec = (
            {"path": str(raw_spec), "required": True}
            if isinstance(raw_spec, str)
            else dict(raw_spec)
        )
        raw_path = spec.get("path")
        if not raw_path:
            raise ValueError("artifact entry has no path")
        path = _project_path(raw_path, kind="artifact")
        required = bool(spec.get("required", True))
        if not path.exists() or not path.is_file():
            record = {
                "path": str(path.relative_to(PROJECT)),
                "required": required,
                "exists": False,
            }
            artifacts.append(record)
            if required:
                artifact_errors.append(
                    f"declared artifact was not created: {raw_path}"
                )
            continue
        artifacts.append(
            {
                "path": str(path.relative_to(PROJECT)),
                "required": required,
                "exists": True,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    report_path = _project_path(
        manifest.get("report") or f"cloud/output/{job_id}.report.json",
        kind="report",
    )
    report = {
        "job_id": job_id,
        "module": module,
        "args": args,
        "manifest_sha256": _sha256(manifest_path),
        "started_at": started,
        "completed_at": _now(),
        "returncode": result.returncode,
        "stdout": result.stdout[-20000:],
        "stderr": result.stderr[-20000:],
        "host": platform.node(),
        "python": sys.version,
        "python_executable": python_executable,
        "inputs": inputs,
        "artifacts": artifacts,
        "artifact_errors": artifact_errors,
        "report_path": str(report_path.relative_to(PROJECT)),
    }
    _atomic_json(report_path, report)
    if result.returncode != 0:
        raise RuntimeError(f"worker job failed; see {report_path}")
    if artifact_errors:
        raise RuntimeError(
            f"worker job omitted required artifacts; see {report_path}"
        )
    return report


def _python_executable(environment: dict[str, str]) -> str:
    """Return an executable interpreter even when Batch clears sys.executable."""

    candidates = (
        sys.executable,
        environment.get("CLOUDSDK_PYTHON"),
        shutil.which("python3", path=environment.get("PATH")),
        "/usr/bin/python3",
    )
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise RuntimeError("no executable Python interpreter is available")


def _validate_inputs(raw_inputs: Any) -> list[dict[str, Any]]:
    if raw_inputs is None:
        return []
    if not isinstance(raw_inputs, list):
        raise ValueError("inputs must be a list")
    verified = []
    for raw_spec in raw_inputs:
        if not isinstance(raw_spec, dict):
            raise ValueError("each input must provide path and sha256")
        raw_path = raw_spec.get("path")
        expected = str(raw_spec.get("sha256") or "").lower()
        if not raw_path or len(expected) != 64:
            raise ValueError("each input must provide path and sha256")
        path = _project_path(raw_path, kind="input")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"declared input does not exist: {raw_path}")
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(
                f"input checksum mismatch for {raw_path}: "
                f"expected {expected}, got {actual}"
            )
        verified.append({
            "path": str(path.relative_to(PROJECT)),
            "size": path.stat().st_size,
            "sha256": actual,
        })
    return verified


def _project_path(raw_path: Any, *, kind: str) -> Path:
    path = (PROJECT / str(raw_path)).resolve()
    if path != PROJECT and PROJECT not in path.parents:
        raise ValueError(f"{kind} escapes project root: {raw_path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = handle.name
    os.replace(temporary, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    args = parser.parse_args()
    report = run_job(Path(args.manifest).resolve())
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
