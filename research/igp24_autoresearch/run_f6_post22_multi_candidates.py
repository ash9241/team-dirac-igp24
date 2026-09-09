#!/usr/bin/env python3
"""Checkpoint the six post-22 F6 multi-orbit pair-resolvent packets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CAMPAIGN = DATA / "campaign_20260727_f627"
CENSUS = CAMPAIGN / "f6_post22_pair_revival_census.jsonl"
PLAN = CAMPAIGN / "f6_post22_multi_wave_plan.json"
CANDIDATES = CAMPAIGN / "f6_post22_multi_wave_candidates.jsonl"
SUMMARY = CAMPAIGN / "f6_post22_multi_wave_candidate_summary.json"
WORKER = ROOT / "pair_sum_one.sage.py"

TASKS = [
    {
        "sourceLabel": "24T3698",
        "sourceR": 8,
        "targetLabel": "24T3836",
        "sourceSubmissionId": "sub_2cdbfc7b5fc9485fba4ec8caceebfadd",
        "sourcePolynomialIndex": 986,
        "sourceCoefficientSha256": "d9d6b22c724962cdd425f5cb1d64cefd1eb79684c392e4b6f4bf39d7b4fa7c34",
    },
    {
        "sourceLabel": "24T3721",
        "sourceR": 8,
        "targetLabel": "24T3899",
        "sourceSubmissionId": "sub_2cdbfc7b5fc9485fba4ec8caceebfadd",
        "sourcePolynomialIndex": 683,
        "sourceCoefficientSha256": "b6e103daa743c9840040a98c289c7774d437cfbd01eafe31b750d13fb4febcb0",
    },
    {
        "sourceLabel": "24T3818",
        "sourceR": 8,
        "targetLabel": "24T3894",
        "sourceSubmissionId": "sub_baaabe99cc4b4071bf54e64c60c22eaa",
        "sourcePolynomialIndex": 209,
        "sourceCoefficientSha256": "0b842675080bc89b058cec67fdca3df1e52ddb69f2a622849d3d86c4bbdeb1c1",
    },
    {
        "sourceLabel": "24T8353",
        "sourceR": 0,
        "targetLabel": "24T8253",
        "sourceSubmissionId": "sub_e449969ded96447cba48d168a5e91f2e",
        "sourcePolynomialIndex": 2,
        "sourceCoefficientSha256": "00d7542dbd7d50e00d79d2f5a84628737919ab6a0153e79867924a5cfa2485b7",
    },
    {
        "sourceLabel": "24T15342",
        "sourceR": 16,
        "targetLabel": "24T15962",
        "sourceSubmissionId": "sub_a5cfcc3d6c9f443ba26a855faff6d4b6",
        "sourcePolynomialIndex": 1,
        "sourceCoefficientSha256": "f33236f724447122601e50cf0c25395bb85be855ba7ee6df459a431809215818",
    },
    {
        "sourceLabel": "24T8353",
        "sourceR": 4,
        "targetLabel": "24T8253",
        "sourceSubmissionId": "sub_2dad8289992d4f0fa9f41163bb2889c7",
        "sourcePolynomialIndex": 1,
        "sourceCoefficientSha256": "acb1cd1f3d62d40053d5ada6c9fb3fdb2459dcc682c49b8b6d617bbd6db0d57d",
    },
]


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_checkpoint(path: Path, row: dict) -> None:
    with path.open("ab") as handle:
        handle.write((canonical_json(row) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def task_key(task: dict) -> str:
    return (
        f"{task['sourceLabel']}|r{task['sourceR']}|"
        f"{task['sourceSubmissionId']}|p{task['sourcePolynomialIndex']}"
    )


def build_plan() -> dict:
    tasks = [{**task, "taskKey": task_key(task)} for task in TASKS]
    if len(tasks) != 6 or len({task["taskKey"] for task in tasks}) != 6:
        raise ValueError("multi task list is not exactly six distinct packets")
    plan = {
        "schemaVersion": "f6-post22-multi-wave-plan-v1",
        "censusPath": str(CENSUS.relative_to(ROOT)),
        "censusSha256": sha256_file(CENSUS),
        "coverageTargetLabels": [
            "24T3836",
            "24T3899",
            "24T3894",
            "24T8253",
            "24T15962",
        ],
        "packetCount": 6,
        "transforms": [1, 2, 3],
        "reduction": "best",
        "nfdisc": True,
        "checkpointEvery": 1,
        "tasks": tasks,
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    payload = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
    if PLAN.exists():
        if PLAN.read_bytes() != payload:
            raise ValueError("existing multi plan differs")
    else:
        atomic_write(PLAN, payload)
    return plan


def parse_result(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def run_task(task: dict, timeout: int) -> dict:
    command = [
        "sage",
        "-python",
        str(WORKER),
        task["sourceSubmissionId"],
        str(task["sourcePolynomialIndex"]),
        "--expected-target",
        task["targetLabel"],
        "--expected-source-hash",
        task["sourceCoefficientSha256"],
        "--orbit-map",
        str(CENSUS),
        "--transforms",
        "1,2,3",
        "--reduce",
        "best",
        "--nfdisc",
        "--all-degree-24",
    ]
    environment = dict(os.environ)
    environment["SAGE_NUM_THREADS"] = "1"
    environment["OPENBLAS_NUM_THREADS"] = "1"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        return {
            **task,
            "taskKey": task_key(task),
            "status": "timeout",
            "wallSeconds": round(time.monotonic() - started, 3),
            "stderrTail": (error.stderr or "")[-1000:]
            if isinstance(error.stderr, str)
            else None,
        }
    result = parse_result(completed.stdout)
    if (
        completed.returncode != 0
        or not isinstance(result, dict)
        or result.get("status") != "certified_multi"
    ):
        return {
            **task,
            "taskKey": task_key(task),
            "status": "worker_error",
            "returnCode": int(completed.returncode),
            "wallSeconds": round(time.monotonic() - started, 3),
            "stderrTail": completed.stderr[-1000:],
            "workerResult": result,
        }
    if (
        result.get("sourceLabel") != task["sourceLabel"]
        or int(result.get("sourceR", -1)) != int(task["sourceR"])
        or result.get("sourceCoefficientSha256")
        != task["sourceCoefficientSha256"]
        or len(result.get("candidates") or []) != 3
    ):
        raise ValueError(f"worker receipt mismatch for {task_key(task)}")
    return {
        **result,
        "taskKey": task_key(task),
        "workerWallSeconds": round(time.monotonic() - started, 3),
        "workerStderrSha256": hashlib.sha256(
            completed.stderr.encode("utf-8")
        ).hexdigest(),
    }


def write_summary(plan: dict) -> dict:
    rows = read_jsonl(CANDIDATES)
    statuses = sorted({str(row["status"]) for row in rows})
    summary = {
        "schemaVersion": "f6-post22-multi-wave-candidate-summary-v1",
        "packetCount": int(plan["packetCount"]),
        "completed": len({str(row["taskKey"]) for row in rows}),
        "statusCounts": {
            status: sum(str(row["status"]) == status for row in rows)
            for status in statuses
        },
        "certifiedMulti": sum(
            str(row["status"]) == "certified_multi" for row in rows
        ),
        "candidateFactors": sum(len(row.get("candidates") or []) for row in rows),
        "candidatesPath": str(CANDIDATES.relative_to(ROOT)),
        "candidatesSha256": sha256_file(CANDIDATES),
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    payload = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
    temporary = SUMMARY.with_suffix(SUMMARY.suffix + f".{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, SUMMARY)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    plan = build_plan()
    completed = {str(row["taskKey"]) for row in read_jsonl(CANDIDATES)}
    for ordinal, task in enumerate(plan["tasks"], 1):
        if task["taskKey"] in completed:
            continue
        row = run_task(task, args.timeout)
        append_checkpoint(CANDIDATES, row)
        print(
            canonical_json(
                {
                    "event": "checkpoint",
                    "ordinal": ordinal,
                    "packetCount": plan["packetCount"],
                    "taskKey": task["taskKey"],
                    "status": row["status"],
                }
            ),
            flush=True,
        )
    summary = write_summary(plan)
    print(canonical_json(summary), flush=True)
    return 0 if int(summary["certifiedMulti"]) == 6 else 2


if __name__ == "__main__":
    raise SystemExit(main())
