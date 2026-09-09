#!/usr/bin/env python3
"""Select unsubmitted siblings from empirically mislabelled construction families."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from routeA.ledger import DEFAULT_DB
from routeA.submit_exploration_batch import validate_validity_candidate


def _rows(paths: Iterable[Path]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            record = validate_validity_candidate(row)
            previous = unique.get(record.candidate_hash)
            if previous is None:
                unique[record.candidate_hash] = dict(row)
    return list(unique.values())


def select_empirical_relabels(
    sources: Iterable[Path],
    output: Path,
    *,
    db_path: Path = DEFAULT_DB,
    minimum_verified: int = 1,
    minimum_relabel_rate: float = 0.5,
    include_unverified_families: bool = False,
    family_prefixes: tuple[str, ...] = (),
    limit: int | None = None,
) -> dict[str, Any]:
    rows = _rows(sources)
    connection = sqlite3.connect(db_path)
    try:
        verified = {
            str(candidate_hash): (int(target_t), bool(accepted))
            for candidate_hash, target_t, accepted in connection.execute(
                "SELECT candidate_hash, verified_t, accepted FROM verification "
                "WHERE verified_t IS NOT NULL"
            )
        }
        committed = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT si.candidate_hash FROM submission_item si "
                "JOIN submission s ON s.batch_uuid=si.batch_uuid WHERE s.dry_run=0"
            )
        }
    finally:
        connection.close()

    family_stats: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        family = str(row.get("recipe_family") or "")
        result = verified.get(str(row["candidate_hash"]))
        if not family or result is None or not result[1]:
            continue
        family_stats[family]["verified"] += 1
        family_stats[family]["relabelled"] += int(
            int(row.get("target_t") or 0) != result[0]
        )

    all_families = {
        str(row.get("recipe_family") or "") for row in rows
        if row.get("recipe_family")
    }
    if family_prefixes:
        all_families = {
            family for family in all_families
            if family.startswith(family_prefixes)
        }
    eligible_families = {
        family
        for family, stats in family_stats.items()
        if family in all_families
        if stats["verified"] >= int(minimum_verified)
        and stats["relabelled"] / stats["verified"]
        >= float(minimum_relabel_rate)
    }
    if include_unverified_families:
        eligible_families |= all_families - family_stats.keys()
    selected = [
        row
        for row in rows
        if str(row.get("recipe_family") or "") in eligible_families
        and str(row["candidate_hash"]) not in committed
    ]
    def priority(row: dict[str, Any]) -> tuple[Any, ...]:
        family = str(row.get("recipe_family") or "")
        stats = family_stats.get(family, Counter())
        verified_count = int(stats["verified"])
        relabel_rate = (
            float(stats["relabelled"]) / verified_count
            if verified_count else 0.0
        )
        return (
        -relabel_rate,
        verified_count,
        family,
        int(row.get("target_t") or 0),
        int(row.get("target_r") or 0),
        str(row["candidate_hash"]),
        )

    selected.sort(key=priority)
    eligible_count = len(selected)
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in selected
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(output)
    return {
        "input_rows": len(rows),
        "verified_families": len(family_stats),
        "eligible_families": len(eligible_families),
        "eligible_candidates": eligible_count,
        "selected_candidates": len(selected),
        "output": str(output),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--minimum-verified", type=int, default=1)
    parser.add_argument("--minimum-relabel-rate", type=float, default=0.5)
    parser.add_argument("--include-unverified-families", action="store_true")
    parser.add_argument("--family-prefix", action="append", default=[])
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.minimum_verified < 1:
        parser.error("--minimum-verified must be positive")
    if not 0.0 <= args.minimum_relabel_rate <= 1.0:
        parser.error("--minimum-relabel-rate must lie in [0, 1]")
    print(json.dumps(select_empirical_relabels(
        args.sources,
        args.output,
        db_path=args.db,
        minimum_verified=args.minimum_verified,
        minimum_relabel_rate=args.minimum_relabel_rate,
        include_unverified_families=args.include_unverified_families,
        family_prefixes=tuple(args.family_prefix),
        limit=args.limit,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
