#!/usr/bin/env python3
"""Build the exact pair-action census in crash-isolated Sage/GAP workers."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"
WORKER = ROOT / "pair_orbit_one.sage.py"
OUTPUT = ROOT / "data" / "pair_orbit_map.jsonl"
FAILURES = ROOT / "data" / "pair_orbit_map_failures.json"


def sources() -> list[tuple[str, int]]:
    with sqlite3.connect(DB_PATH) as conn:
        return [
            (str(label), int(t))
            for label, t in conn.execute(
                "SELECT DISTINCT label,t FROM verifications ORDER BY t"
            )
        ]


def run_one(source: tuple[str, int], timeout: int) -> tuple[dict | None, dict | None]:
    label, t = source
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
        return None, {"sourceLabel": label, "sourceT": t, "error": "timeout"}
    if completed.returncode != 0:
        return None, {
            "sourceLabel": label,
            "sourceT": t,
            "error": f"exit {completed.returncode}: {completed.stderr[-2000:]}",
        }
    try:
        row = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return None, {
            "sourceLabel": label,
            "sourceT": t,
            "error": f"invalid worker output: {exc}: {completed.stdout[-1000:]}",
        }
    return row, None


def write_checkpoint(rows: list[dict], failures: list[dict]) -> None:
    rows.sort(key=lambda row: row["sourceT"])
    temporary = OUTPUT.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(OUTPUT)
    FAILURES.write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    source_rows = sources()
    rows = []
    failures = []
    if OUTPUT.exists() and not args.fresh:
        rows = [json.loads(line) for line in OUTPUT.read_text(encoding="utf-8").splitlines() if line]
    mapped_labels = {row["sourceLabel"] for row in rows}
    pending_sources = [source for source in source_rows if source[0] not in mapped_labels]
    completed_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(run_one, source, args.timeout): source for source in pending_sources
        }
        for future in concurrent.futures.as_completed(futures):
            row, failure = future.result()
            if row is not None:
                rows.append(row)
            if failure is not None:
                failures.append(failure)
            completed_count += 1
            if completed_count % 10 == 0:
                write_checkpoint(rows, failures)
            if completed_count % 25 == 0 or completed_count == len(pending_sources):
                print(
                    f"mapped {len(mapped_labels) + completed_count}/{len(source_rows)} "
                    f"(ok={len(rows)}, failed={len(failures)})",
                    file=sys.stderr,
                    flush=True,
                )
    write_checkpoint(rows, failures)
    print(
        json.dumps(
            {
                "sources": len(source_rows),
                "mapped": len(rows),
                "failed": len(failures),
                "length24Sources": sum(row["length24OrbitCount"] > 0 for row in rows),
                "length24Orbits": sum(row["length24OrbitCount"] for row in rows),
                "nontrivialSources": sum(
                    any(target["targetLabel"] != row["sourceLabel"] for target in row["targets"])
                    for row in rows
                ),
                "output": str(OUTPUT),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
