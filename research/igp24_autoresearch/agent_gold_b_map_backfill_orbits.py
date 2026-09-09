#!/usr/bin/env python3
"""Compute agent-local exact pair-action maps for unmapped backfill labels."""

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
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
GLOBAL_MAP = DATA / "pair_orbit_map.jsonl"
WORKER = ROOT / "pair_orbit_one.sage.py"
OUTPUT = DATA / "agent_gold_b_backfill_pair_orbits.jsonl"
COMBINED = DATA / "agent_gold_b_combined_pair_orbit_map.jsonl"
FAILURES = DATA / "agent_gold_b_backfill_pair_orbit_failures.jsonl"
SUMMARY = DATA / "agent_gold_b_backfill_pair_orbits_summary.json"
FRONTIER_SUMMARY = DATA / "agent_gold_b_backfill_closure_frontier_summary.json"
OLD_CUTOFF = 1784628497.513
HISTORICAL_CREATED_BEFORE = "2026-07-21T00:00:00Z"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def map_one(label: str, t: int, timeout: int) -> tuple[dict | None, dict | None]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            ["sage", "-python", str(WORKER), label, str(t)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, {"sourceLabel": label, "sourceT": t, "status": "timeout", "wallSeconds": timeout}
    wall = round(time.monotonic() - started, 3)
    if completed.returncode != 0:
        return None, {"sourceLabel": label, "sourceT": t, "status": "error", "stderrTail": completed.stderr[-1200:], "wallSeconds": wall}
    try:
        row = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return None, {"sourceLabel": label, "sourceT": t, "status": "invalid_output", "error": str(exc), "wallSeconds": wall}
    row["status"] = "certified"
    row["wallSeconds"] = wall
    return row, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--snapshot-upper", type=float)
    args = parser.parse_args()
    if args.workers < 1 or args.timeout < 1:
        parser.error("workers and timeout must be positive")
    if args.snapshot_upper is None:
        snapshot = json.loads(FRONTIER_SUMMARY.read_text(encoding="utf-8"))
        upper = float(snapshot["snapshotUpperEpochInclusive"])
    else:
        upper = args.snapshot_upper

    mapped = {str(row["sourceLabel"]) for row in read_jsonl(GLOBAL_MAP)}
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        labels = [
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT DISTINCT v.label,v.t
                FROM verifications AS v
                JOIN submissions AS s USING(submission_id)
                WHERE s.synced_at>? AND s.synced_at<=? AND s.created_at<?
                  AND v.scoreable=1
                ORDER BY v.t
                """,
                (OLD_CUTOFF, upper, HISTORICAL_CREATED_BEFORE),
            )
            if str(row[0]) not in mapped
        ]
    prior = {str(row["sourceLabel"]): row for row in read_jsonl(OUTPUT)}
    pending = [(label, t) for label, t in labels if label not in prior]
    failures = read_jsonl(FAILURES)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(map_one, label, t, args.timeout): (label, t) for label, t in pending}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            row, failure = future.result()
            if row is not None:
                prior[str(row["sourceLabel"])] = row
            if failure is not None:
                failures.append(failure)
            if index % 10 == 0 or index == len(pending):
                write_jsonl(OUTPUT, sorted(prior.values(), key=lambda item: int(item["sourceT"])))
                write_jsonl(FAILURES, failures)
                print(f"mapped {index}/{len(pending)} ok={len(prior)} failed={len(failures)}", flush=True)
    write_jsonl(OUTPUT, sorted(prior.values(), key=lambda item: int(item["sourceT"])))
    write_jsonl(FAILURES, failures)
    rows = list(prior.values())
    combined_by_label = {
        str(row["sourceLabel"]): row
        for row in [*read_jsonl(GLOBAL_MAP), *rows]
    }
    write_jsonl(
        COMBINED,
        sorted(combined_by_label.values(), key=lambda item: int(item["sourceT"])),
    )
    summary = {
        "snapshotUpperEpochInclusive": upper,
        "requestedUnmappedLabels": len(labels),
        "certified": len(rows),
        "failed": len(failures),
        "uniqueLength24": sum(len(row.get("targets") or []) == 1 for row in rows),
        "uniqueNonselfLength24": sum(
            len(row.get("targets") or []) == 1
            and str(row["targets"][0]["targetLabel"]) != str(row["sourceLabel"])
            for row in rows
        ),
        "anyLength24": sum(bool(row.get("targets")) for row in rows),
        "output": str(OUTPUT.resolve()),
        "outputSha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        "combinedMap": str(COMBINED.resolve()),
        "combinedMapSha256": hashlib.sha256(COMBINED.read_bytes()).hexdigest(),
        "failures": str(FAILURES.resolve()),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
