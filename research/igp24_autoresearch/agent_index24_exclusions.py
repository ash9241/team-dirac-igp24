#!/usr/bin/env python3
"""Freeze original/pair/triple representation identities as exclusions only."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
GROUP_INPUT = DATA / "agent_index24_group_input.jsonl"
OUTPUT = DATA / "agent_index24_exclusion_identities.jsonl"
SUMMARY = DATA / "agent_index24_exclusion_summary.json"
PAIR_MAPS = (
    DATA / "pair_orbit_map.jsonl",
    DATA / "agent_gold_b_combined_pair_orbit_map.jsonl",
    DATA / "agent_page19_pair_orbit_combined.jsonl",
)
TRIPLE_MAPS = tuple(
    DATA / f"agent_gold_a_triple_orbit_shard{index}.jsonl" for index in range(4)
)


def rows(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, text: str):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    owned = {
        str(row["label"])
        for row in rows(GROUP_INPUT)
        if bool(row["isOwnedSource"])
    }
    excluded = {label: {label} for label in owned}
    provenance = defaultdict(list)
    for label in owned:
        provenance[label].append("original_degree_24_action")
    pair_edges = 0
    for path in PAIR_MAPS:
        for row in rows(path):
            source = str(row["sourceLabel"])
            if source not in owned:
                continue
            for target in row.get("targets", []):
                label = str(target["targetLabel"])
                if label not in excluded[source]:
                    pair_edges += 1
                excluded[source].add(label)
            provenance[source].append(str(path.resolve()))
    triple_edges = 0
    for path in TRIPLE_MAPS:
        for row in rows(path):
            source = str(row["sourceLabel"])
            if source not in owned:
                continue
            for target in row.get("targets", []):
                label = str(target["targetLabel"])
                if label not in excluded[source]:
                    triple_edges += 1
                excluded[source].add(label)
            provenance[source].append(str(path.resolve()))
    output = [
        {
            "excludedTargetLabels": sorted(
                excluded[label], key=lambda value: int(value[3:])
            ),
            "sourceLabel": label,
        }
        for label in sorted(owned, key=lambda value: int(value[3:]))
    ]
    atomic(
        OUTPUT,
        "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in output),
    )
    summary = {
        "ownedSourceLabels": len(owned),
        "pairIdentityEdgesAdded": pair_edges,
        "sourceArtifacts": [
            {"path": str(path.resolve()), "sha256": sha(path)}
            for path in (*PAIR_MAPS, *TRIPLE_MAPS)
            if path.exists()
        ],
        "totalExcludedSourceTargetIdentities": sum(len(values) for values in excluded.values()),
        "tripleIdentityEdgesAdded": triple_edges,
    }
    atomic(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
