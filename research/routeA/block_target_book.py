#!/usr/bin/env python3
"""Build an exact, owned-pair-aware target book for a GAP block shape."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.scheduler import load_owned_pairs


PROJECT = Path(__file__).resolve().parent.parent


def build_target_book(
    progress_labels: Iterable[dict[str, Any]],
    census_rows: Iterable[dict[str, Any]],
    *,
    block_shape: Iterable[int],
    owned_pairs: set[tuple[int, int]],
    max_team_count: int = 1,
) -> list[dict[str, Any]]:
    wanted = tuple(sorted(set(int(value) for value in block_shape)))
    census = {
        int(row["t"]): row
        for row in census_rows
        if tuple(int(value) for value in row.get("block_sizes", [])) == wanted
    }
    records = []
    for label in progress_labels:
        target = int(label["t"])
        group = census.get(target)
        if group is None:
            continue
        for signature in label.get("signatures", []):
            roots = int(signature["r"])
            team_count = int(signature.get("teamCount", 0))
            pair = (target, roots)
            if (
                bool(signature.get("baseline"))
                or team_count > max_team_count
                or pair in owned_pairs
            ):
                continue
            records.append({
                "t": target,
                "r": roots,
                "team_count": team_count,
                "score_ceiling": 2.0 ** (-team_count),
                "minimum_disc_abs": signature.get("minimumDiscAbs"),
                "group_order": group["order"],
                "solvable": bool(group["solvable"]),
                "block_sizes": group["block_sizes"],
                "block_quotients": group["block_quotients"],
                "evidence": "gap-exact",
            })
    return sorted(
        records,
        key=lambda row: (
            row["team_count"],
            int(row["group_order"]),
            -row["r"],
            row["t"],
        ),
    )


def _jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("census")
    parser.add_argument("output")
    parser.add_argument(
        "--progress",
        default=str(PROJECT / "daemon" / "data" / "all_progress.json"),
    )
    parser.add_argument("--block-shape", default="2,4,8")
    parser.add_argument("--max-team-count", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    progress = json.loads(Path(args.progress).read_text(encoding="utf-8"))
    with Ledger(args.db) as ledger:
        owned_pairs = load_owned_pairs(PROJECT) | ledger.owned_pairs()
        records = build_target_book(
            progress,
            _jsonl(args.census),
            block_shape=[int(value) for value in args.block_shape.split(",") if value],
            owned_pairs=owned_pairs,
            max_team_count=args.max_team_count,
        )
    if args.limit > 0:
        records = records[:args.limit]
    Path(args.output).write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records)
        + ("\n" if records else ""),
        encoding="utf-8",
    )
    print(json.dumps({
        "pairs": len(records),
        "gold": sum(record["team_count"] == 0 for record in records),
        "raids": sum(record["team_count"] == 1 for record in records),
        "solo_equivalent_ceiling": sum(record["score_ceiling"] for record in records),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
