#!/usr/bin/env python3
"""Wait for a prior range, then run an index-24 plan range with bounded workers."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def count_outputs(path: Path) -> int:
    return sum(1 for _ in path.glob("gp_job_*.jsonl")) if path.is_dir() else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-dir", type=Path)
    parser.add_argument("--wait-count", type=int, default=0)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--stop", type=int, required=True)
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--transforms", default="1,2,3")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--gp-stack", type=int, default=536870912)
    args = parser.parse_args()
    if args.start < 0 or args.stop <= args.start:
        raise ValueError("range must satisfy 0 <= start < stop")
    plan_rows = sum(1 for line in args.plan.open(encoding="utf-8") if line.strip())
    if args.stop > plan_rows:
        raise ValueError(f"range stop {args.stop} exceeds {plan_rows} plan rows")
    if args.wait_dir:
        while True:
            current = count_outputs(args.wait_dir)
            print(
                json.dumps(
                    {"event": "waiting", "current": current, "required": args.wait_count},
                    sort_keys=True,
                ),
                flush=True,
            )
            if current >= args.wait_count:
                break
            time.sleep(args.poll_seconds)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    indices = list(range(args.start, args.stop))
    if args.reverse:
        indices.reverse()
    environment = dict(os.environ)
    environment["OPENBLAS_NUM_THREADS"] = "1"
    environment["SAGE_NUM_THREADS"] = "1"

    def run(index: int) -> tuple[int, int, str]:
        command = [
            sys.executable,
            str(ROOT / "run_live_index24_pair_resolvent_gp_20260813.py"),
            "--plan", str(args.plan),
            "--db", str(args.db),
            "--job-index", str(index),
            "--output-dir", str(args.output_dir),
            "--transforms", args.transforms,
            "--timeout", str(args.timeout),
            "--gp-stack", str(args.gp_stack),
        ]
        completed = subprocess.run(command, text=True, capture_output=True, env=environment)
        detail = (completed.stdout + completed.stderr)[-2000:]
        return index, completed.returncode, detail

    failures = []
    completed_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run, index): index for index in indices}
        for future in concurrent.futures.as_completed(futures):
            index, returncode, detail = future.result()
            completed_count += 1
            if returncode:
                failures.append({"index": index, "returncode": returncode, "detail": detail})
                print(json.dumps({"event": "failure", **failures[-1]}, sort_keys=True), flush=True)
            elif completed_count % 100 == 0 or completed_count == len(indices):
                print(
                    json.dumps(
                        {
                            "event": "progress",
                            "completed": completed_count,
                            "failures": len(failures),
                            "total": len(indices),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    summary = {
        "event": "complete",
        "completed": completed_count,
        "failures": failures,
        "outputCount": count_outputs(args.output_dir),
        "total": len(indices),
    }
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
