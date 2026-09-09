#!/usr/bin/env python3
"""Split a campaign matrix into disjoint, reindexed array matrices."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--shards", type=int, required=True)
    args = parser.parse_args()
    if args.shards < 1:
        raise ValueError("--shards must be positive")

    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    tasks = matrix.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("campaign matrix has no tasks")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sizes = []
    for shard_index in range(args.shards):
        selected = tasks[shard_index::args.shards]
        shard = dict(matrix)
        shard["parent_matrix"] = str(args.matrix)
        shard["shard_index"] = shard_index
        shard["shard_count"] = args.shards
        shard["tasks"] = [dict(task, index=index) for index, task in enumerate(selected)]
        shard["task_count"] = len(selected)
        output = args.output_dir / f"matrix-{shard_index:02d}.json"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=args.output_dir, delete=False
        ) as handle:
            json.dump(shard, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(output)
        sizes.append(len(selected))
    print(json.dumps({
        "source_tasks": len(tasks),
        "shards": args.shards,
        "minimum_shard_tasks": min(sizes),
        "maximum_shard_tasks": max(sizes),
        "output_dir": str(args.output_dir),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
