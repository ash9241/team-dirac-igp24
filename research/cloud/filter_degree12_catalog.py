#!/usr/bin/env python3
"""Write an atomic degree-12 catalog subset for selected base groups."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base", action="append", type=int, required=True)
    parser.add_argument("--limit-per-base", type=int)
    args = parser.parse_args()
    if args.limit_per_base is not None and args.limit_per_base < 1:
        parser.error("--limit-per-base must be positive")
    bases = set(args.base)
    rows = []
    for line in args.source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if int(row["base_t"]) in bases:
            rows.append(row)
    rows.sort(key=lambda row: (int(row["base_t"]), int(row["disc_abs"]), row["label"]))
    if args.limit_per_base is not None:
        retained = []
        counts: Counter[int] = Counter()
        for row in rows:
            base_t = int(row["base_t"])
            if counts[base_t] >= args.limit_per_base:
                continue
            retained.append(row)
            counts[base_t] += 1
        rows = retained
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=args.output.parent, delete=False
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temporary = Path(handle.name)
    temporary.replace(args.output)
    print(json.dumps({"output": str(args.output), "rows": len(rows), "bases": sorted(bases)}))


if __name__ == "__main__":
    main()
