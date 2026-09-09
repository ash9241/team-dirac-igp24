#!/usr/bin/env python3
"""Merge character-discovery JSONL files by their canonical seed key."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from cloud.merge_character_campaign import _row_quality, _row_sort_key, _seed_key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()

    by_seed: dict[str, dict] = {}
    raw_rows = 0
    for path in args.sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            raw_rows += 1
            key = _seed_key(row)
            previous = by_seed.get(key)
            if previous is None or _row_quality(row) > _row_quality(previous):
                by_seed[key] = row

    rows = sorted(by_seed.values(), key=lambda row: _row_sort_key(row, "discoveries"))
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=args.destination.parent, delete=False
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temporary = Path(handle.name)
    temporary.replace(args.destination)
    print(json.dumps({
        "destination": str(args.destination),
        "raw_rows": raw_rows,
        "unique_rows": len(rows),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
