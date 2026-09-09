#!/usr/bin/env sage -python
"""Fast exact order census for the frozen owned/gold 24T universe.

Group order is a complete-preserving first filter: groups with unequal orders
cannot be isomorphic.  More expensive invariants are deliberately deferred to
the surviving source/target pairs.
"""

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=6)
    parser.add_argument("--checkpoint-every", type=int, default=100)
    args = parser.parse_args()

    selected = [
        row
        for index, row in enumerate(read_jsonl(args.input))
        if index % args.shard_count == args.shard_index
    ]
    output = []
    for index, row in enumerate(selected, start=1):
        try:
            group = libgap.TransitiveGroup(24, int(row["t"]))
            result = {
                **row,
                "order": str(libgap.Size(group)),
                "status": "certified",
            }
        except Exception as exc:
            result = {
                **row,
                "error": f"{type(exc).__name__}: {exc}",
                "status": "error",
            }
        output.append(result)
        if args.checkpoint_every and index % args.checkpoint_every == 0:
            write_jsonl(args.output, output)
            print(
                json.dumps(
                    {"completed": index, "shard": args.shard_index, "total": len(selected)}
                ),
                flush=True,
            )
    write_jsonl(args.output, output)
    errors = sum(row["status"] != "certified" for row in output)
    print(json.dumps({"errors": errors, "rows": len(output), "shard": args.shard_index}))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
