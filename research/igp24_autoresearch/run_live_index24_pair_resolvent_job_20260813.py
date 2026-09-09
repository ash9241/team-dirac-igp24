#!/usr/bin/env python3
"""Run one current-value unordered-pair resolvent plan job."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--job-index", type=int, required=True, help="zero-based plan row")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--transforms", default="1,2,3")
    parser.add_argument("--nfdisc", action="store_true")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.plan.read_text().splitlines() if line.strip()]
    job = rows[args.job_index]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"job_{args.job_index:04d}_{job['jobId']}.jsonl"
    if output.exists():
        print(json.dumps({"event": "already_complete", "output": str(output)}))
        return 0
    command = [
        "/usr/bin/sage", "-python", "pair_sum_one.sage.py",
        str(job["sourceSubmissionId"]), str(job["sourcePolynomialIndex"]),
        "--orbit-map", str(job["orbitMap"]),
        "--expected-source-hash", str(job["coefficientSha256"]),
        "--all-degree-24", "--transforms", args.transforms,
        "--reduce", "best", "--output-jsonl", str(output),
    ]
    if args.nfdisc:
        command.append("--nfdisc")
    environment = dict(os.environ)
    environment.update({"SAGE_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    completed = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
