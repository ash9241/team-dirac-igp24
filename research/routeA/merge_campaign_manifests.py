#!/usr/bin/env python3
"""Merge score-campaign JSONL manifests without losing alternate candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def merge_rows(paths: list[Path]) -> list[dict[str, Any]]:
    """Keep every distinct polynomial, ordered deterministically by target."""

    by_candidate: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in load_jsonl(path):
            key = str(row.get("candidate_hash") or row["coefficients"])
            previous = by_candidate.get(key)
            if previous is None:
                by_candidate[key] = row
                continue
            # Prefer the richer/newer certificate if two manifests contain
            # the same polynomial with different metadata.
            previous_quality = (
                bool(previous.get("submission_ready")),
                bool(previous.get("exact_compatibility_proven")),
                len(previous.get("modular_witnesses") or []),
            )
            quality = (
                bool(row.get("submission_ready")),
                bool(row.get("exact_compatibility_proven")),
                len(row.get("modular_witnesses") or []),
            )
            if quality > previous_quality:
                by_candidate[key] = row
    return sorted(
        by_candidate.values(),
        key=lambda row: (
            int(row["target_t"]),
            int(row["target_r"]),
            int(row.get("field_disc_abs") or row.get("estimated_nfdisc_abs") or 0),
            str(row.get("candidate_hash") or row["coefficients"]),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()

    paths = [args.destination, *args.sources]
    rows = merge_rows(paths)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.destination.with_suffix(args.destination.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(args.destination)
    print(json.dumps({
        "destination": str(args.destination),
        "sources": [str(path) for path in paths],
        "rows": len(rows),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
