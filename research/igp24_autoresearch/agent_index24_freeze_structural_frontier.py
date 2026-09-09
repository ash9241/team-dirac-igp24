#!/usr/bin/env python3
"""Freeze the F6 index-24 subgroup frontier beyond known pair identities."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def read_patterns(patterns: list[str]):
    for pattern in patterns:
        for filename in glob.glob(pattern):
            for line in Path(filename).read_text().splitlines():
                if line.strip():
                    yield json.loads(line)


def write_jsonl(path: Path, rows: list[dict]) -> str:
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered)
    temporary.replace(path)
    return hashlib.sha256(rendered.encode()).hexdigest()


def write_json(path: Path, payload: dict) -> str:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered)
    temporary.replace(path)
    return hashlib.sha256(rendered.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--pair-maps", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    candidates = list(read_patterns([str(args.candidates)]))
    census = list(read_patterns([str(args.census)]))
    pair_edges = {
        (int(row["sourceT"]), int(target["targetT"]))
        for row in read_patterns(args.pair_maps)
        for target in row.get("targets", [])
    }
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    action_existential = action_safe = action_deterministic = 0
    isomorphic = nonisomorphic = subgroup_classes = 0
    for row in census:
        if row.get("status") == "certified_nonisomorphic":
            nonisomorphic += 1
            continue
        if row.get("status") != "certified_isomorphic":
            continue
        isomorphic += 1
        source_t = int(row["sourceT"])
        source_label = str(row["sourceLabel"])
        source_rs = sorted(int(value) for value in row["sourceR"])
        gold_rs = {int(value) for value in row["goldR"]}
        subgroup_classes += len(row.get("subgroupClasses", []))
        for subgroup in row.get("subgroupClasses", []):
            target_t = int(subgroup["actualTargetT"])
            if target_t == source_t or (source_t, target_t) in pair_edges:
                continue
            for source_r in source_rs:
                mapped = tuple(sorted({
                    int(profile["targetR"])
                    for profile in subgroup.get("profiles", [])
                    if int(profile["sourceR"]) == source_r
                }))
                if not mapped or not set(mapped).intersection(gold_rs):
                    continue
                safe = set(mapped) <= gold_rs
                deterministic = safe and len(mapped) == 1
                action_existential += 1
                action_safe += int(safe)
                action_deterministic += int(deterministic)
                key = (source_t, source_r, target_t, mapped)
                grouped[key].append({
                    "autOrbitIndex": int(subgroup["autOrbitIndex"]),
                    "subgroupClassIdentitySha256": str(subgroup["subgroupClassIdentitySha256"]),
                })

    frontier = []
    for (source_t, source_r, target_t, mapped), classes in sorted(grouped.items()):
        gold_rs = sorted({
            int(value)
            for row in candidates
            if int(row["sourceT"]) == source_t and int(row["targetT"]) == target_t
            for value in row["goldR"]
        })
        safe = set(mapped) <= set(gold_rs)
        frontier.append({
            "actionClassCount": len(classes),
            "allCompatibleClassesGold": safe,
            "deterministicTargetR": mapped[0] if safe and len(mapped) == 1 else None,
            "executableInvariant": None,
            "goldR": gold_rs,
            "mappedTargetR": list(mapped),
            "sourceLabel": f"24T{source_t}",
            "sourceR": source_r,
            "sourceT": source_t,
            "subgroupClasses": classes,
            "targetLabel": f"24T{target_t}",
            "targetT": target_t,
        })
    frontier_sha = write_jsonl(args.output, frontier)
    summary = {
        "actionClassCounts": {
            "deterministic": action_deterministic,
            "existential": action_existential,
            "safe": action_safe,
        },
        "candidateRows": len(candidates),
        "certifiedRows": len(census),
        "coverageComplete": len(census) == len(candidates),
        "frontierSha256": frontier_sha,
        "isomorphic": isomorphic,
        "nonisomorphic": nonisomorphic,
        "pairIdentityEdgesExcluded": len(pair_edges),
        "structuralRouteCounts": {
            "deterministic": sum(row["deterministicTargetR"] is not None for row in frontier),
            "existential": len(frontier),
            "safe": sum(row["allCompatibleClassesGold"] for row in frontier),
        },
        "subgroupClassesEnumerated": subgroup_classes,
    }
    summary_sha = write_json(args.summary, summary)
    print(json.dumps({"summarySha256": summary_sha, **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
