#!/usr/bin/env python3
"""Emit a Google Cloud Batch array definition for a campaign package."""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any

from cloud.generator_worker import PROJECT


def build_batch_job(
    matrix_path: str | Path,
    output_path: str | Path,
    *,
    bucket: str,
    package_object: str,
    results_prefix: str,
    job_id: str | None = None,
    project_id: str = "dirac-phm",
    region: str = "us-central1",
    machine_type: str = "e2-standard-8",
    parallelism: int = 256,
    tasks_per_node: int = 8,
    cpu_milli: int = 1000,
    memory_mib: int = 3072,
    max_retries: int = 2,
    max_run_seconds: int = 21600,
    bootstrap_profile: str = "full",
    spot: bool = True,
    service_account: str | None = None,
) -> dict[str, Any]:
    matrix_path = _project_path(matrix_path, "matrix")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    task_count = int(matrix.get("task_count") or 0)
    if task_count < 1 or task_count != len(matrix.get("tasks") or []):
        raise ValueError("campaign matrix has an invalid task count")
    bucket = _plain_bucket(bucket)
    package_object = _object_path(package_object)
    results_prefix = _object_path(results_prefix).rstrip("/")
    parallelism = min(task_count, int(parallelism))
    if parallelism < 1 or tasks_per_node < 1:
        raise ValueError("parallelism and tasks_per_node must be positive")
    if tasks_per_node > 20:
        raise ValueError("Cloud Batch allows at most 20 tasks per VM")
    campaign = str(matrix.get("campaign_id") or "character-campaign")
    if bootstrap_profile not in {"full", "pari", "pari217"}:
        raise ValueError("bootstrap_profile must be 'full', 'pari', or 'pari217'")
    job_id = _batch_id(job_id or campaign)
    matrix_relative = str(matrix_path.relative_to(PROJECT))

    script = _task_script(
        package_object=package_object,
        results_prefix=results_prefix,
        matrix_relative=matrix_relative,
        bootstrap_profile=bootstrap_profile,
    )
    policy: dict[str, Any] = {"machineType": machine_type}
    if spot:
        policy["provisioningModel"] = "SPOT"
    allocation: dict[str, Any] = {"instances": [{"policy": policy}]}
    if service_account:
        allocation["serviceAccount"] = {"email": str(service_account)}
    config = {
        "taskGroups": [{
            "taskSpec": {
                "runnables": [{"script": {"text": script}}],
                "computeResource": {
                    "cpuMilli": int(cpu_milli),
                    "memoryMib": int(memory_mib),
                },
                "volumes": [{
                    "gcs": {"remotePath": bucket},
                    "mountPath": "/mnt/igp24",
                }],
                "maxRetryCount": int(max_retries),
                "maxRunDuration": f"{int(max_run_seconds)}s",
            },
            "taskCount": task_count,
            "parallelism": parallelism,
            "taskCountPerNode": int(tasks_per_node),
        }],
        "allocationPolicy": allocation,
        "labels": {
            "workload": "igp24-character",
            "campaign": _label_value(campaign),
        },
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }
    output_path = Path(output_path)
    _atomic_json(output_path, config)
    summary = {
        "campaign_id": campaign,
        "job_id": job_id,
        "project_id": project_id,
        "region": region,
        "task_count": task_count,
        "parallelism": parallelism,
        "tasks_per_node": int(tasks_per_node),
        "maximum_vms": math.ceil(parallelism / int(tasks_per_node)),
        "machine_type": machine_type,
        "provisioning_model": "SPOT" if spot else "STANDARD",
        "bootstrap_profile": bootstrap_profile,
        "bucket": bucket,
        "package_uri": f"gs://{bucket}/{package_object}",
        "results_uri": f"gs://{bucket}/{results_prefix}/",
        "config": str(output_path),
        "submit_command": (
            f"gcloud batch jobs submit {shlex.quote(job_id)} "
            f"--project {shlex.quote(project_id)} "
            f"--location {shlex.quote(region)} "
            f"--config {shlex.quote(str(output_path))}"
        ),
    }
    _atomic_json(output_path.with_suffix(output_path.suffix + ".report.json"), summary)
    return summary


def _task_script(
    *,
    package_object: str,
    results_prefix: str,
    matrix_relative: str,
    bootstrap_profile: str = "full",
) -> str:
    # Batch executes script-text runnables with /bin/sh, so keep the prologue
    # POSIX-compatible. Debian's /bin/sh is dash and rejects `set -o pipefail`.
    if bootstrap_profile == "pari":
        bootstrap = '''
  if [ ! -f /var/lib/igp24-bootstrap-pari-v1 ] ||
     ! command -v gp >/dev/null 2>&1; then
    rm -f /var/lib/igp24-bootstrap-pari-v1
    apt-get -o Acquire::Retries=5 update -qq
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y -qq python3 pari-gp pari-galdata >/dev/null
    command -v gp >/dev/null
    touch /var/lib/igp24-bootstrap-pari-v1
  fi
'''
    elif bootstrap_profile == "pari217":
        bootstrap = '''
  if [ ! -f /var/lib/igp24-bootstrap-pari217-v1 ] ||
     ! /opt/pari217/bin/gp --version 2>&1 | grep -q "2.17.2"; then
    rm -f /var/lib/igp24-bootstrap-pari217-v1
    apt-get -o Acquire::Retries=5 update -qq
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y -qq python3 build-essential curl ca-certificates libgmp-dev >/dev/null
    rm -rf /tmp/pari-2.17.2 /tmp/pari-2.17.2.tar.gz
    curl -fsSL --retry 5 -o /tmp/pari-2.17.2.tar.gz https://pari.math.u-bordeaux.fr/pub/pari/unix/pari-2.17.2.tar.gz
    echo "7d30578f5cf97b137a281f4548d131aafc0cde86bcfd10cc1e1bd72a81e65061  /tmp/pari-2.17.2.tar.gz" | sha256sum -c - >/dev/null
    tar -xzf /tmp/pari-2.17.2.tar.gz -C /tmp
    cd /tmp/pari-2.17.2
    ./Configure --prefix=/opt/pari217 --with-gmp >/dev/null 2>&1
    make -j"$(nproc)" gp >/dev/null 2>&1
    make install >/dev/null 2>&1
    /opt/pari217/bin/gp --version 2>&1 | grep -q "2.17.2"
    ln -sf /opt/pari217/bin/gp /usr/bin/gp
    touch /var/lib/igp24-bootstrap-pari217-v1
  fi
'''
    elif bootstrap_profile == "full":
        bootstrap = '''
  if [ ! -f /var/lib/igp24-bootstrap-v1 ] ||
     ! /usr/bin/python3 -c "import sympy" >/dev/null 2>&1 ||
     ! command -v gp >/dev/null 2>&1 ||
     ! command -v gap >/dev/null 2>&1; then
    rm -f /var/lib/igp24-bootstrap-v1
    apt-get -o Acquire::Retries=5 update -qq
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -o Acquire::Retries=5 install -y -qq python3 python3-sympy pari-gp pari-galdata gap gap-transgrp >/dev/null
    /usr/bin/python3 -c "import sympy"
    command -v gp >/dev/null
    command -v gap >/dev/null
    touch /var/lib/igp24-bootstrap-v1
  fi
'''
    else:
        raise ValueError("bootstrap_profile must be 'full', 'pari', or 'pari217'")
    return f"""set -eu
task=$(printf '%05d' "${{BATCH_TASK_INDEX}}")
attempt="${{BATCH_TASK_RETRY_ATTEMPT:-0}}"
work="/tmp/igp24-${{task}}-${{attempt}}"
rm -rf "${{work}}"
mkdir -p "${{work}}/project"
cp "/mnt/igp24/{package_object}" "${{work}}/campaign.tar.gz"
tar -xzf "${{work}}/campaign.tar.gz" -C "${{work}}/project"
sudo flock /var/lock/igp24-bootstrap.lock bash -eu -c '
{bootstrap}
'
export IGP24_GP=/usr/bin/gp
export IGP24_GAP=/usr/bin/gap
unset IGP24_API_KEY IGP24_API_KEY_FILE
cd "${{work}}/project"
python3 -m cloud.run_array_task \\
  --matrix {shlex.quote(matrix_relative)} \\
  --task-index "${{BATCH_TASK_INDEX}}" \\
  --result-archive "${{work}}/result.tar.gz"
mkdir -p "/mnt/igp24/{results_prefix}"
cp "${{work}}/result.tar.gz" "/mnt/igp24/{results_prefix}/task-${{task}}.tar.gz"
"""


def _plain_bucket(value: str) -> str:
    bucket = str(value).removeprefix("gs://").strip("/")
    if not bucket or "/" in bucket:
        raise ValueError("bucket must be a plain Cloud Storage bucket name")
    return bucket


def _object_path(value: str) -> str:
    path = str(value).removeprefix("gs://").lstrip("/")
    if not path or ".." in Path(path).parts:
        raise ValueError(f"invalid Cloud Storage object path: {value}")
    return path


def _batch_id(value: str) -> str:
    clean = re.sub(r"[^a-z0-9-]+", "-", str(value).lower()).strip("-")
    if not clean:
        raise ValueError("job id contains no usable characters")
    return clean[:63]


def _label_value(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", str(value).lower()).strip("-_")
    return (clean or "campaign")[:63]


def _project_path(raw: str | Path, kind: str) -> Path:
    path = (PROJECT / str(raw)).resolve()
    if path != PROJECT and PROJECT not in path.parents:
        raise ValueError(f"{kind} escapes project root: {raw}")
    return path


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = handle.name
    Path(temporary).replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--package-object", required=True)
    parser.add_argument("--results-prefix", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--project", default="dirac-phm")
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--machine-type", default="e2-standard-8")
    parser.add_argument("--parallelism", type=int, default=256)
    parser.add_argument("--tasks-per-node", type=int, default=8)
    parser.add_argument("--cpu-milli", type=int, default=1000)
    parser.add_argument("--memory-mib", type=int, default=3072)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--max-run-seconds", type=int, default=21600)
    parser.add_argument(
        "--bootstrap-profile",
        choices=("full", "pari", "pari217"),
        default="full",
    )
    parser.add_argument("--standard", action="store_true")
    parser.add_argument("--service-account")
    args = parser.parse_args()
    summary = build_batch_job(
        args.matrix,
        args.output,
        bucket=args.bucket,
        package_object=args.package_object,
        results_prefix=args.results_prefix,
        job_id=args.job_id,
        project_id=args.project,
        region=args.region,
        machine_type=args.machine_type,
        parallelism=args.parallelism,
        tasks_per_node=args.tasks_per_node,
        cpu_milli=args.cpu_milli,
        memory_mib=args.memory_mib,
        max_retries=args.max_retries,
        max_run_seconds=args.max_run_seconds,
        bootstrap_profile=args.bootstrap_profile,
        spot=not args.standard,
        service_account=args.service_account,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
