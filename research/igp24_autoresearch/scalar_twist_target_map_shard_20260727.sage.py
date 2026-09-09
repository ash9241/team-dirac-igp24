#!/usr/bin/env sage -python
"""Parallel resumable target-system-map shard for the scalar-twist census."""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MAIN = ROOT / "scalar_twist_saved_f5_f6_induced_action_census_20260727.sage.py"


def load_main():
    specification = importlib.util.spec_from_file_location("scalar_twist_main", MAIN)
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


def jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def source_action_maps(target_ts: set[int]) -> set[int]:
    mapped = set()
    for path_text in glob.glob(str(DATA / "**" / "*.jsonl"), recursive=True):
        path = Path(path_text)
        if "action" not in path.name and "twist" not in path.name:
            continue
        try:
            for row in jsonl(path):
                if (
                    "sourceT" in row
                    and "systems" in row
                    and "systemCount" in row
                    and int(row["sourceT"]) in target_ts
                ):
                    mapped.add(int(row["sourceT"]))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    return mapped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=4)
    parser.add_argument("--exclude-targets", default="")
    parser.add_argument("--output-tag", default="")
    arguments = parser.parse_args()
    if not 0 <= arguments.shard_index < arguments.shard_count:
        raise ValueError("invalid shard index")
    module = load_main()

    saved = []
    for path_text in glob.glob(module.F5_GLOB):
        saved.extend(jsonl(Path(path_text)))
    f6 = json.loads(module.F6.read_text())
    saved.extend(
        row
        for row in f6["actionRows"]
        if row.get("transitive") and row.get("targetT") is not None
    )
    target_ts = {int(row["targetT"]) for row in saved}
    mapped = source_action_maps(target_ts)
    mapped.update(int(row["targetT"]) for row in jsonl(module.TARGET_MAP))
    excluded = {
        int(value)
        for value in arguments.exclude_targets.split(",")
        if value.strip()
    }
    remaining = sorted(target_ts - mapped - excluded)
    assigned = remaining[arguments.shard_index :: arguments.shard_count]
    tag = f"_{arguments.output_tag}" if arguments.output_tag else ""
    output = DATA / (
        "scalar_twist_saved_f5_f6_target_system_map_20260727"
        f"{tag}_part{arguments.shard_index}of{arguments.shard_count}.jsonl"
    )
    rows = jsonl(output)
    complete = {int(row["targetT"]) for row in rows}
    assigned = [target_t for target_t in assigned if target_t not in complete]
    for index, target_t in enumerate(assigned, start=1):
        rows.append(module.target_system_row(target_t))
        module.write_jsonl_atomic(output, rows)
        print(
            module.canonical_json(
                {
                    "completed": index,
                    "remainingInShardAtStart": len(assigned),
                    "shard": arguments.shard_index,
                    "targetT": target_t,
                }
            ),
            flush=True,
        )
    print(
        module.canonical_json(
            {
                "rows": len(rows),
                "shard": arguments.shard_index,
                "status": "complete",
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
