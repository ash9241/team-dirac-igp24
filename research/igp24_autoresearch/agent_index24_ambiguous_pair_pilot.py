#!/usr/bin/env python3
"""Resolve the remaining F6 missing-pair signature ambiguity arithmetically.

Only sources with one exact length-24 unordered-pair orbit are handled here.
For every locally owned source polynomial, ``pair_sum_one.sage.py`` constructs
and factors the exact pair-sum resolvent; the resulting polynomial's exact real
root count decides whether the structurally ambiguous route lands in the frozen
gold signature set.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess
import time
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


def job_key(row: dict) -> tuple[str, int, str]:
    return (
        str(row["sourceSubmissionId"]),
        int(row["sourcePolynomialIndex"]),
        str(row["targetLabel"]),
    )


def load_jobs(shards: list[Path], route_mode: str = "ambiguous") -> list[dict]:
    route_rows = []
    for shard in shards:
        for source in read_jsonl(shard):
            if source.get("status") != "certified" or int(source["length24OrbitCount"]) != 1:
                continue
            selected_routes = [
                route for route in source.get("routes", [])
                if bool(route["allCompatibleClassesGold"])
                == (route_mode == "safe")
            ]
            for route in selected_routes:
                route_rows.append((shard, source, route))

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    jobs = []
    for shard, source, route in route_rows:
        source_rows = connection.execute(
            """
            SELECT p.submission_id,p.polynomial_index,p.coefficient_hash
            FROM polynomials p JOIN verifications v
              USING(submission_id,polynomial_index)
            WHERE v.label=? AND v.r=? AND v.scoreable=1
            ORDER BY p.submission_id,p.polynomial_index
            """,
            (str(source["sourceLabel"]), int(route["sourceR"])),
        ).fetchall()
        for owned in source_rows:
            jobs.append(
                {
                    "goldR": sorted(int(value) for value in route["goldR"]),
                    "mappedTargetR": sorted(int(value) for value in route["mappedTargetR"]),
                    "orbitIndex": int(route["orbitIndex"]),
                    "orbitMap": str(shard.resolve()),
                    "sourceCoefficientSha256": str(owned["coefficient_hash"]),
                    "sourceLabel": str(source["sourceLabel"]),
                    "sourcePolynomialIndex": int(owned["polynomial_index"]),
                    "sourceR": int(route["sourceR"]),
                    "sourceSubmissionId": str(owned["submission_id"]),
                    "targetLabel": str(route["targetLabel"]),
                    "targetT": int(route["targetT"]),
                    "routeMode": route_mode,
                }
            )
    connection.close()
    unique = {job_key(row): row for row in jobs}
    return [unique[key] for key in sorted(unique)]


def run_job(job: dict, timeout: int) -> dict:
    command = [
        "sage", "-python", str(WORKER),
        str(job["sourceSubmissionId"]), str(job["sourcePolynomialIndex"]),
        "--expected-target", str(job["targetLabel"]),
        "--orbit-map", str(job["orbitMap"]),
        "--transforms", "1,2,3",
        "--reduce", "best",
    ]
    command.append("--nfdisc")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            **job,
            "error": f"TimeoutExpired after {timeout}s",
            "stderrTail": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
            "status": "error",
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    result = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            result = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if completed.returncode != 0 or not isinstance(result, dict) or result.get("status") != "certified":
        return {
            **job,
            "error": f"worker exit {completed.returncode}",
            "result": result,
            "stderrTail": completed.stderr[-1000:],
            "status": "error",
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    realized_r = int(result["targetR"])
    exact_hit = realized_r in set(job["goldR"])
    return {
        **job,
        "candidate": result,
        "candidateSha256": hashlib.sha256(result["coefficientLine"].encode()).hexdigest(),
        "exactFrozenGoldHit": exact_hit,
        "realizedTargetR": realized_r,
        "status": "exact_frozen_gold_hit" if exact_hit else "exact_signature_miss",
        "stderrTail": completed.stderr[-1000:],
        "wallSeconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--route-mode",
        choices=("ambiguous", "safe"),
        default="ambiguous",
        help="run structurally ambiguous routes or all-compatible-class-safe routes",
    )
    args = parser.parse_args()

    jobs = load_jobs(args.shards, args.route_mode)
    completed = {job_key(row): row for row in read_jsonl(args.output)}
    pending = [job for job in jobs if job_key(job) not in completed]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_job, job, args.timeout): job for job in pending}
        for count, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            row = future.result()
            completed[job_key(row)] = row
            write_jsonl(args.output, [completed[key] for key in sorted(completed)])
            print(json.dumps({
                "completed": len(completed),
                "errors": sum(value["status"] == "error" for value in completed.values()),
                "event": "ambiguous_pair_checkpoint",
                "hits": sum(value["status"] == "exact_frozen_gold_hit" for value in completed.values()),
                "pendingProcessed": count,
                "pendingThisRun": len(pending),
                "totalJobs": len(jobs),
            }), flush=True)
    return 0 if all(row["status"] != "error" for row in completed.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
