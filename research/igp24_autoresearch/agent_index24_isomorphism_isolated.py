#!/usr/bin/env python3
"""Finish the exact F6 census with one crash-isolated GAP process per pair.

The fast default radical method is attempted first.  GAP collector failures are
contained to that one child and retried with the stable Morphium path.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "agent_index24_isomorphism_census.sage.py"


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def key(row: dict):
    return int(row["sourceT"]), int(row["targetT"])


def attempt(
    input_path: Path,
    total: int,
    candidate_index: int,
    method: str,
    timeout: int,
    temporary_directory: Path,
):
    output = temporary_directory / f"row_{candidate_index}_{method}.jsonl"
    command = [
        "sage",
        "-python",
        str(WORKER),
        "--input",
        str(input_path),
        "--output",
        str(output),
        "--shard-index",
        str(candidate_index),
        "--shard-count",
        str(total),
        "--checkpoint-every",
        "1",
        "--isomorphism-method",
        method,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # The Sage worker checkpoints its single certified row before GAP exits.
        # Some GAP processes then hang during interpreter cleanup, so preserve a
        # mathematically complete row even though subprocess.run reached timeout.
        rows = read_jsonl(output)
        diagnostic = {
            "method": method,
            "status": "timeout_with_output" if len(rows) == 1 else "timeout",
            "wallSeconds": round(time.monotonic() - started, 3),
        }
        return (rows[0] if len(rows) == 1 else None), diagnostic
    rows = read_jsonl(output)
    diagnostic = {
        "exitCode": int(completed.returncode),
        "method": method,
        "status": "completed" if rows else "missing_output",
        "stderrTail": completed.stderr[-500:],
        "wallSeconds": round(time.monotonic() - started, 3),
    }
    return (rows[0] if len(rows) == 1 else None), diagnostic


def run_one(
    input_path: Path,
    total: int,
    candidate_index: int,
    timeout: int,
    temporary_directory: Path,
):
    diagnostics = []
    row, diagnostic = attempt(
        input_path, total, candidate_index, "default", timeout, temporary_directory
    )
    diagnostics.append(diagnostic)
    if row is not None and row.get("status") != "error":
        row["isolatedIsomorphismMethod"] = "default"
        row["isolatedAttempts"] = diagnostics
        return candidate_index, row
    row, diagnostic = attempt(
        input_path, total, candidate_index, "old", timeout, temporary_directory
    )
    diagnostics.append(diagnostic)
    if row is None:
        return candidate_index, {
            "candidateIndex": candidate_index,
            "isolatedAttempts": diagnostics,
            "status": "error",
        }
    row["isolatedIsomorphismMethod"] = "old"
    row["isolatedAttempts"] = diagnostics
    return candidate_index, row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--preload", type=Path, nargs="*", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--failures", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    args = parser.parse_args()
    candidates = read_jsonl(args.input)
    index_by_key = {key(row): index for index, row in enumerate(candidates)}
    if len(index_by_key) != len(candidates):
        raise RuntimeError("candidate input contains duplicate source-target pairs")

    completed = {}
    for path in args.preload:
        for row in read_jsonl(path):
            if row.get("status") == "error":
                continue
            candidate_index = index_by_key.get(key(row))
            if candidate_index is not None:
                completed[candidate_index] = row
    for row in read_jsonl(args.output):
        if row.get("status") == "error":
            continue
        candidate_index = index_by_key.get(key(row))
        if candidate_index is not None:
            completed[candidate_index] = row

    pending = [index for index in range(len(candidates)) if index not in completed]
    failures = []
    with tempfile.TemporaryDirectory(prefix="f6_iso_") as temporary_name:
        temporary_directory = Path(temporary_name)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    run_one,
                    args.input.resolve(),
                    len(candidates),
                    index,
                    args.timeout,
                    temporary_directory,
                ): index
                for index in pending
            }
            for done_count, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                index, row = future.result()
                if row.get("status") == "error":
                    failures.append({**candidates[index], **row})
                else:
                    completed[index] = row
                if done_count % args.checkpoint_every == 0 or done_count == len(pending):
                    write_jsonl(args.output, [completed[i] for i in sorted(completed)])
                    write_jsonl(args.failures, failures)
                    print(json.dumps({
                        "completed": len(completed),
                        "failures": len(failures),
                        "pendingProcessed": done_count,
                        "pendingTotal": len(pending),
                        "total": len(candidates),
                    }), flush=True)
    return 0 if len(completed) == len(candidates) and not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
