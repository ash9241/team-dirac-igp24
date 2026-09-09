#!/usr/bin/env python3
"""Run the current live conditional pair-resolvent portfolio in parallel."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run_one(task: dict, timeout: int) -> dict:
    started = time.monotonic()
    command = list(task["cmd"])
    command[0] = "/usr/bin/sage"
    output = ROOT / task["output"]
    log_directory = ROOT / "data" / "current_gold_pair_portfolio_20260813"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / f"{task['id']}.log"
    if output.exists():
        return {
            "id": task["id"],
            "live": task["live"],
            "output": task["output"],
            "status": "already_complete",
        }
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        log_path.write_text(
            completed.stdout + "\n--- STDERR ---\n" + completed.stderr,
            encoding="utf-8",
        )
        status = "complete" if completed.returncode == 0 and output.exists() else "failed"
        return {
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "exitCode": completed.returncode,
            "id": task["id"],
            "live": task["live"],
            "log": str(log_path.relative_to(ROOT)),
            "output": task["output"],
            "status": status,
        }
    except subprocess.TimeoutExpired as error:
        log_path.write_text(
            (error.stdout or "") + "\n--- STDERR ---\n" + (error.stderr or ""),
            encoding="utf-8",
        )
        return {
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "id": task["id"],
            "live": task["live"],
            "log": str(log_path.relative_to(ROOT)),
            "output": task["output"],
            "status": "timeout",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    all_tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    tasks = [
        task
        for index, task in enumerate(all_tasks)
        if index % args.shard_count == args.shard_index
    ]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, task, args.timeout): task for task in tasks}
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            results.append(row)
            print(json.dumps(row, separators=(",", ":"), sort_keys=True), flush=True)
    summary = ROOT / "data" / f"current_gold_pair_portfolio_20260813_shard{args.shard_index}.json"
    summary.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if all(row["status"] in {"complete", "already_complete"} for row in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
