#!/usr/bin/env python3
"""Build crash-isolated GAP cycle-type catalogs for multi-orbit targets."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MULTI_RESULTS = ROOT / "data" / "pair_sum_multi_candidates.jsonl"
WORKER = ROOT / "group_cycle_types_one.sage.py"
OUTPUT = ROOT / "data" / "group_cycle_types.jsonl"


def labels() -> list[tuple[str, int]]:
    result = set()
    for line in MULTI_RESULTS.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        for target in json.loads(line).get("orbitTargets") or []:
            result.add((str(target["targetLabel"]), int(target["targetT"])))
    return sorted(result, key=lambda item: item[1])


def run_one(target: tuple[str, int], timeout: int) -> dict:
    label, t = target
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
        return {"label": label, "t": t, "status": "timeout"}
    if completed.returncode != 0:
        return {
            "label": label,
            "t": t,
            "status": "error",
            "error": completed.stderr[-2000:],
        }
    try:
        row = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {"label": label, "t": t, "status": "invalid_output", "error": str(exc)}
    row["status"] = "certified"
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    targets = labels()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(run_one, target, args.timeout): target for target in targets}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            row = future.result()
            rows.append(row)
            print(
                f"cataloged {index}/{len(targets)}: {row['label']} status={row['status']}",
                file=sys.stderr,
                flush=True,
            )
    rows.sort(key=lambda row: int(row["t"]))
    temporary = OUTPUT.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(OUTPUT)
    summary = {
        "targets": len(targets),
        "certified": sum(row["status"] == "certified" for row in rows),
        "failed": sum(row["status"] != "certified" for row in rows),
        "output": str(OUTPUT),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
