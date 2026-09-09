#!/usr/bin/env python3
"""Recover a source-label-diverse exact subfield bank in six shards."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
WORKER = ROOT / "agent_non12_subfield_worker.sage.py"
INVENTORY = DATA / "agent_non12_tower_owned_inventory.json"
OUTPUT = DATA / "agent_non12_recoverable_subfields.jsonl"
SUMMARY = DATA / "agent_non12_recoverable_subfields_summary.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def run(source: Path, output: Path) -> dict:
    completed = subprocess.run(
        ["sage", "-python", str(WORKER), "--input", str(source), "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"subfield shard failed: {completed.stderr[-2400:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> int:
    inventory = json.loads(INVENTORY.read_text())["labels"]
    strict = [row for row in inventory if not bool(row["has12x2BlockSystem"])]
    tasks = []
    for row in strict:
        presentation = row["bestPresentation"]
        tasks.append(
            {
                "sourceLabel": row["label"],
                "submissionId": presentation["submissionId"],
                "polynomialIndex": int(presentation["polynomialIndex"]),
                "degrees": [int(value) for value in row["recoverableSubfieldDegrees"]],
            }
        )
    tasks.sort(key=lambda row: (int(row["sourceLabel"][3:]), row["submissionId"], row["polynomialIndex"]))
    temporary_root = Path(tempfile.mkdtemp(prefix="non12_subfields_"))
    try:
        inputs, outputs = [], []
        for shard in range(6):
            source = temporary_root / f"tasks_{shard}.jsonl"
            output = temporary_root / f"results_{shard}.jsonl"
            source.write_text(
                "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in tasks[shard::6]),
                encoding="utf-8",
            )
            inputs.append(source)
            outputs.append(output)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(run, source, output) for source, output in zip(inputs, outputs)]
            shard_summaries = [future.result() for future in futures]
        rows = []
        for output in outputs:
            rows.extend(json.loads(line) for line in output.read_text().splitlines() if line.strip())
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    rows.sort(
        key=lambda row: (
            int(row["subfieldDegree"]),
            str(row["galoisGroup"]["label"]),
            int(row["realRoots"]),
            int(row["sourceLabel"][3:]),
            str(row["coefficientSha256"]),
        )
    )
    atomic_text(
        OUTPUT,
        "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows),
    )
    summary = {
        "strictNonF1OwnedSourceLabelsAttempted": len(tasks),
        "sourceDegreeTasks": sum(len(row["degrees"]) for row in tasks),
        "certifiedRecoverableSubfieldRows": len(rows),
        "distinctReducedCoefficientHashes": len({row["coefficientSha256"] for row in rows}),
        "distinctFieldDiscriminants": len({(row["subfieldDegree"], row["fieldDiscriminantAbs"]) for row in rows}),
        "representedSourceLabels": len({row["sourceLabel"] for row in rows}),
        "byDegreeRows": dict(sorted(Counter(int(row["subfieldDegree"]) for row in rows).items())),
        "byDegreeDistinctHashes": {
            str(degree): len({row["coefficientSha256"] for row in rows if int(row["subfieldDegree"]) == degree})
            for degree in (3, 4, 6, 8)
        },
        "byGroup": dict(sorted(Counter(str(row["galoisGroup"]["label"]) for row in rows).items())),
        "byRealRoots": dict(sorted(Counter(int(row["realRoots"]) for row in rows).items())),
        "iteratedLowDegreeRows": sum(bool(row["galoisGroup"]["properBlockSizes"]) for row in rows),
        "output": str(OUTPUT),
        "outputSha256": sha256(OUTPUT),
        "shards": shard_summaries,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
