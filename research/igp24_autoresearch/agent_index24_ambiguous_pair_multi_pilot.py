#!/usr/bin/env python3
"""Construct exact multi-factor packets for ambiguous or safe F6 pair routes."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
WORKER = ROOT / "pair_sum_one.sage.py"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def key(row: dict) -> tuple[str, int]:
    return str(row["sourceSubmissionId"]), int(row["sourcePolynomialIndex"])


def load_jobs(shards: list[Path], route_mode: str = "ambiguous") -> list[dict]:
    source_pairs = {}
    for shard in shards:
        for source in read_jsonl(shard):
            if source.get("status") != "certified" or int(source["length24OrbitCount"]) <= 1:
                continue
            for route in source.get("routes", []):
                if bool(route["allCompatibleClassesGold"]) != (route_mode == "safe"):
                    continue
                source_pairs[(str(source["sourceLabel"]), int(route["sourceR"]))] = shard
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    jobs = []
    for (label, source_r), shard in sorted(source_pairs.items()):
        for row in connection.execute(
            """
            SELECT p.submission_id,p.polynomial_index,p.coefficient_hash
            FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
            WHERE v.label=? AND v.r=? AND v.scoreable=1
            ORDER BY p.submission_id,p.polynomial_index
            """,
            (label, source_r),
        ):
            jobs.append({
                "orbitMap": str(shard.resolve()),
                "sourceCoefficientSha256": str(row["coefficient_hash"]),
                "sourceLabel": label,
                "sourcePolynomialIndex": int(row["polynomial_index"]),
                "sourceR": source_r,
                "sourceSubmissionId": str(row["submission_id"]),
            })
    connection.close()
    return jobs


def run_job(job: dict, timeout: int) -> dict:
    command = [
        "sage", "-python", str(WORKER),
        str(job["sourceSubmissionId"]), str(job["sourcePolynomialIndex"]),
        "--orbit-map", str(job["orbitMap"]),
        "--all-degree-24", "--transforms", "1,2,3", "--reduce", "best",
        "--nfdisc",
    ]
    try:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {**job, "status": "timeout"}
    result = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            result = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(result, dict):
        return {
            **job, "status": "invalid_output",
            "stderrTail": completed.stderr[-1000:],
            "workerExitCode": completed.returncode,
        }
    return {
        **job, **result,
        "stderrTail": completed.stderr[-1000:],
        "workerExitCode": completed.returncode,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--route-mode",
        choices=("ambiguous", "safe"),
        default="ambiguous",
        help="run structurally ambiguous routes or all-compatible-class-safe routes",
    )
    args = parser.parse_args()
    jobs = load_jobs(args.shards, args.route_mode)
    completed = {key(row): row for row in read_jsonl(args.output)}
    pending = [job for job in jobs if key(job) not in completed]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_job, job, args.timeout): job for job in pending}
        for count, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            row = future.result()
            completed[key(row)] = row
            write_jsonl(args.output, [completed[value] for value in sorted(completed)])
            print(json.dumps({
                "certified": sum(row.get("status") == "certified_multi" for row in completed.values()),
                "completed": len(completed),
                "pendingProcessed": count,
                "pendingThisRun": len(pending),
                "totalJobs": len(jobs),
            }), flush=True)
    return 0 if all(row.get("status") == "certified_multi" for row in completed.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
