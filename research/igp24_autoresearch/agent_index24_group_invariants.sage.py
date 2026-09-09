#!/usr/bin/env sage -python
"""Exact abstract-group invariants for owned and frozen-gold 24T labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sage.all import libgap


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def invariant_one(row: dict) -> dict:
    group = libgap.TransitiveGroup(24, int(row["t"]))
    order = int(libgap.Size(group))
    small_id = None
    if bool(libgap.IdGroupsAvailable(order)):
        small_id = [int(value) for value in libgap.IdGroup(group)]
    return {
        **row,
        "abelianInvariants": sorted(int(value) for value in libgap.AbelianInvariants(group)),
        "centerOrder": int(libgap.Size(libgap.Center(group))),
        "derivedOrder": int(libgap.Size(libgap.DerivedSubgroup(group))),
        "isAbelian": bool(libgap.IsAbelian(group)),
        "isPerfect": bool(libgap.IsPerfectGroup(group)),
        "isSolvable": bool(libgap.IsSolvableGroup(group)),
        "order": order,
        "smallGroupId": small_id,
        "status": "certified",
        "structureDescription": str(libgap.StructureDescription(group)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=6)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    args = parser.parse_args()
    rows = [
        row
        for index, row in enumerate(read_jsonl(args.input))
        if index % args.shard_count == args.shard_index
    ]
    output = []
    for index, row in enumerate(rows, start=1):
        try:
            value = invariant_one(row)
        except Exception as exc:
            value = {
                **row,
                "error": f"{type(exc).__name__}: {exc}",
                "status": "error",
            }
        output.append(value)
        if args.checkpoint_every and index % args.checkpoint_every == 0:
            write_jsonl(args.output, output)
            print(json.dumps({"completed": index, "shard": args.shard_index, "total": len(rows)}), flush=True)
    write_jsonl(args.output, output)
    errors = sum(row["status"] != "certified" for row in output)
    print(json.dumps({"errors": errors, "rows": len(output), "shard": args.shard_index}))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
