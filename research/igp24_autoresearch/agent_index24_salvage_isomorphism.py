#!/usr/bin/env python3
"""Merge certified isolated child rows before their temporary directory expires."""

from __future__ import annotations

import argparse
import glob
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
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidates = read_jsonl(args.candidates)
    order = {
        (int(row["sourceT"]), int(row["targetT"])): index
        for index, row in enumerate(candidates)
    }
    merged = {}
    paths = []
    for pattern in args.inputs:
        paths.extend(Path(value) for value in glob.glob(pattern))
    for path in paths:
        for row in read_jsonl(path):
            if row.get("status") == "error":
                continue
            row_key = (int(row["sourceT"]), int(row["targetT"]))
            if row_key in order:
                merged[row_key] = row
    rows = sorted(merged.values(), key=lambda row: order[(int(row["sourceT"]), int(row["targetT"]))])
    write_jsonl(args.output, rows)
    print(json.dumps({"certifiedRows": len(rows), "inputFiles": len(set(paths))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
