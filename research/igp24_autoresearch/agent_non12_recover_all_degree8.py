#!/usr/bin/env python3
"""Recover degree-8 subfields from every owned non-12 structural source label."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import agent_non12_recover_subfields as shared


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUT = DATA / "agent_non12_all_degree8_subfields.jsonl"
SUMMARY = DATA / "agent_non12_all_degree8_subfields_summary.json"


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    inventory = json.loads(shared.INVENTORY.read_text())["labels"]
    tasks = []
    for row in inventory:
        if 8 not in [int(value) for value in row["recoverableSubfieldDegrees"]]:
            continue
        presentation = row["bestPresentation"]
        tasks.append(
            {
                "sourceLabel": row["label"],
                "submissionId": presentation["submissionId"],
                "polynomialIndex": int(presentation["polynomialIndex"]),
                "degrees": [8],
            }
        )
    tasks.sort(key=lambda row: int(row["sourceLabel"][3:]))
    temporary_root = Path(tempfile.mkdtemp(prefix="all_degree8_subfields_"))
    try:
        paths = []
        for shard in range(6):
            source = temporary_root / f"tasks_{shard}.jsonl"
            output = temporary_root / f"results_{shard}.jsonl"
            source.write_text(
                "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in tasks[shard::6]),
                encoding="utf-8",
            )
            paths.append((source, output))
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(shared.run, source, output) for source, output in paths]
            shards = [future.result() for future in futures]
        rows = [
            json.loads(line)
            for _source, output in paths
            for line in output.read_text().splitlines()
            if line.strip()
        ]
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    rows.sort(key=lambda row: (str(row["galoisGroup"]["label"]), int(row["realRoots"]), str(row["coefficientSha256"])))
    atomic_text(OUTPUT, "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows))
    target = [row for row in rows if row["galoisGroup"]["label"] == "8T42"]
    summary = {
        "ownedSourceLabelsAttempted": len(tasks),
        "certifiedDegree8Rows": len(rows),
        "distinctDegree8Hashes": len({row["coefficientSha256"] for row in rows}),
        "group8T42Rows": len(target),
        "group8T42DistinctHashes": len({row["coefficientSha256"] for row in target}),
        "group8T42RealRootCounts": dict(sorted(Counter(int(row["realRoots"]) for row in target).items())),
        "group8T42SourceLabels": sorted({row["sourceLabel"] for row in target}, key=lambda label: int(label[3:])),
        "output": str(OUTPUT),
        "outputSha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        "shards": shards,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
