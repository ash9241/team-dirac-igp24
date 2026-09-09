#!/usr/bin/env python3
"""Merge computed missing D4 profiles into the exhaustive profile catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.base.read_text(encoding="utf-8"))
    by_label = {str(row["label"]): row for row in payload["groups"]}
    added = set()
    for path in args.supplement:
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                row = json.loads(raw)
                label = str(row["label"])
                by_label[label] = row
                added.add(label)
    payload["groups"] = sorted(by_label.values(), key=lambda row: int(row["t"]))
    payload["compatibleCount"] = len(payload["groups"])
    payload["emergencyProfileSupplementLabels"] = sorted(
        added, key=lambda value: int(value[3:])
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "added": len(added),
                "groups": len(payload["groups"]),
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
