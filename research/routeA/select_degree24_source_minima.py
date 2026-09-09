#!/usr/bin/env python3
"""Select the lowest-discriminant degree-24 source for each exact (T,r) pair."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    best: dict[tuple[int, int], dict] = {}
    for line in args.source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (int(row["source_t"]), int(row["source_r"]))
        previous = best.get(key)
        rank = (int(row["disc_abs"]), str(row["candidate_hash"]))
        if previous is None or rank < (
            int(previous["disc_abs"]), str(previous["candidate_hash"])
        ):
            best[key] = row
    rows = [best[key] for key in sorted(best)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=args.output.parent, delete=False
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temporary = Path(handle.name)
    temporary.replace(args.output)
    print(json.dumps({
        "source": str(args.source),
        "output": str(args.output),
        "rows": len(rows),
        "source_groups": len({key[0] for key in best}),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
