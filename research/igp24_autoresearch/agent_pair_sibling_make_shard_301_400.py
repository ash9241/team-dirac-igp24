#!/usr/bin/env python3
"""Materialize stable pair-sibling ranks 301--400 without reranking 1--200."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import agent_pair_sibling_frontier as frontier


ROOT = Path(__file__).resolve().parent
EXISTING = ROOT / "data" / "agent_gold_a_cross100_forced_frontier.jsonl"
OUTPUT = ROOT / "data" / "agent_pair_sibling_frontier_shard_301_400.jsonl"
CUTOFF = 1784628497.513


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    existing = frontier.read_jsonl(EXISTING)
    if len(existing) != 200:
        raise RuntimeError(f"shared first frontier has {len(existing)} rows, expected 200")
    if any(int(row["priorityTier"]) != 2 for row in existing):
        raise RuntimeError("shared first frontier is not the stable fallback ordering")
    if [int(row["stableExactGateRank"]) for row in existing] != list(range(1, 201)):
        raise RuntimeError("shared first frontier ranks drifted")

    tasks, audit, _orbits = frontier.snapshot_tasks(CUTOFF)
    if audit["eligibleBeforeGate"] != 5365:
        raise RuntimeError("immutable source snapshot no longer reconstructs to 5365")
    for index, row in enumerate(existing, start=1):
        task = tasks[index - 1]
        if frontier.source_key(row) != frontier.source_key(task):
            raise RuntimeError(f"shared ordering drift at rank {index}")
        if int(row["sourceSnapshotRank"]) != index:
            raise RuntimeError(f"shared source rank drift at rank {index}")

    priority_snapshot = float(existing[0]["prioritySnapshotUnix"])
    shard = []
    for rank, task in enumerate(tasks[300:400], start=301):
        shard.append(
            {
                **task,
                "sourceSnapshotRank": rank,
                "priorityTier": 2,
                "priorityKind": "stable_open_label_fallback",
                "forcedExactPairAcrossCompatibleClasses": False,
                "stableExactGateRank": rank,
                "sourceSnapshotCutoffEpoch": CUTOFF,
                "prioritySnapshotUnix": priority_snapshot,
            }
        )
    frontier.write_jsonl_atomic(OUTPUT, shard)
    print(
        json.dumps(
            {
                "rows": len(shard),
                "rankStart": 301,
                "rankEnd": 400,
                "cutoffEpoch": CUTOFF,
                "sharedFirst200Sha256": sha256_file(EXISTING),
                "shardPath": str(OUTPUT.relative_to(ROOT)),
                "shardSha256": sha256_file(OUTPUT),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
