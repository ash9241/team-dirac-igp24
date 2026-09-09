#!/usr/bin/env python3
"""Freeze unresolved exact-isomorphism candidates against a completed prefix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_jsonl(path: Path):
    if not path.exists():
        return []
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
    parser.add_argument("--completed", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    completed = set()
    for path in args.completed:
        for row in read_jsonl(path):
            if row.get("status") != "error":
                completed.add((int(row["sourceT"]), int(row["targetT"])))
    pending = [
        row for row in read_jsonl(args.input)
        if (int(row["sourceT"]), int(row["targetT"])) not in completed
    ]
    write_jsonl(args.output, pending)
    print(json.dumps({"completed": len(completed), "pending": len(pending)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
