#!/usr/bin/env python3
"""Run and stage the current rank-11 character search bank offline."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_auditor():
    path = ROOT / "agent_gold_a_run_cross1500_stage1_odd.py"
    spec = importlib.util.spec_from_file_location("rank11_auditor", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


AUDIT = load_auditor()


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bank", type=Path, default=ROOT / "data" / "current_rank11_character_search_bank_20260812.jsonl"
    )
    parser.add_argument(
        "--results", type=Path, default=ROOT / "data" / "current_rank11_character_search_results_20260812.jsonl"
    )
    parser.add_argument(
        "--hits", type=Path, default=ROOT / "data" / "current_rank11_character_search_hits_20260812.jsonl"
    )
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "outbox" / "current_rank11_character_gold_20260812.txt"
    )
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    tasks = AUDIT.load_bank(args.bank)
    completed = AUDIT.load_completed(args.results)
    tasks = [task for task in tasks if task["globalCommandIndex"] not in completed]
    for task in tasks:
        output = Path(task["output"])
        if not output.is_absolute():
            output = ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
    known_hashes = AUDIT.staged_hashes()
    staged_pairs = {
        (str(row["targetLabel"]), int(row["targetR"]))
        for row in (
            json.loads(line)
            for line in args.hits.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    } if args.hits.exists() else set()
    counts = {"certified": 0, "completed": 0, "failed": 0, "staged": 0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_task = {
            pool.submit(AUDIT.execute, task, args.timeout): task for task in tasks
        }
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    **{key: value for key, value in task.items() if key != "command"},
                    "auditErrors": [f"runner exception: {type(exc).__name__}: {exc}"],
                    "certifiedCandidate": None,
                    "outputParseStatus": "runner_exception",
                }
            candidate = row.pop("certifiedCandidate", None)
            row["exactCertified"] = candidate is not None and not row.get("auditErrors")
            row["staged"] = False
            row["workerFailure"] = row.get("outputParseStatus") in {
                "missing", "invalid", "runner_exception"
            } or bool(row.get("auditErrors"))
            counts["completed"] += 1
            counts["failed"] += int(row["workerFailure"])
            if row["exactCertified"]:
                counts["certified"] += 1
                line = str(candidate["candidateCoefficientLine"])
                digest = hashlib.sha256(line.encode()).hexdigest()
                pair = (task["targetLabel"], task["targetR"])
                if pair not in staged_pairs and digest not in known_hashes:
                    args.manifest.parent.mkdir(parents=True, exist_ok=True)
                    with args.manifest.open("a", encoding="utf-8") as handle:
                        handle.write(line + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    staged_pairs.add(pair)
                    known_hashes.add(digest)
                    row["staged"] = True
                    counts["staged"] += 1
                    append(
                        args.hits,
                        {
                            "candidateCoefficientLine": line,
                            "candidateFieldDiscriminantAbs": candidate["fieldDiscriminantAbs"],
                            "candidateSha256": digest,
                            "targetLabel": pair[0],
                            "targetR": pair[1],
                        },
                    )
            append(args.results, row)
            if counts["completed"] % 10 == 0 or row["staged"]:
                print(json.dumps({"event": "progress", **counts}, sort_keys=True), flush=True)

    print(json.dumps({"event": "complete", **counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
