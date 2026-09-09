#!/usr/bin/env python3
"""Select a breadth-first batch of validity-certified subfield products."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.select_subfield_exploration import DEFAULT_ATLAS, _base_opportunity
from routeA.submit_exploration_batch import validate_validity_candidate


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _structural_key(row: dict[str, Any]) -> tuple[Any, ...]:
    parameters = row.get("parameters") or {}
    factors = tuple(sorted(
        (
            int(item["subfield_degree"]),
            int(item["subfield_index"]),
            int(item["subfield_shift"]),
        )
        for item in parameters["subfield_factors"]
    ))
    return (
        int(row["base_t"]),
        str(row.get("source_field_label") or ""),
        factors,
        int(row["norm_squareclass"]),
        int(row["target_r"]),
    )


def select_subfield_products(
    sources: Sequence[Path],
    output: Path,
    *,
    atlas_path: Path = DEFAULT_ATLAS,
    db_path: str | Path = DEFAULT_DB,
    limit: int = 1000,
) -> dict[str, Any]:
    if not 1 <= int(limit) <= 1000:
        raise ValueError("limit must be in [1, 1000]")
    opportunity = _base_opportunity(atlas_path)
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    input_rows = valid_rows = committed = 0
    with Ledger(db_path) as ledger:
        for source in sources:
            for row in _load_jsonl(source):
                input_rows += 1
                record = validate_validity_candidate(row)
                valid_rows += 1
                if ledger.candidate_committed(record.candidate_hash):
                    committed += 1
                    continue
                key = _structural_key(row)
                previous = by_key.get(key)
                if previous is None or int(row["field_disc_abs"]) < int(previous["field_disc_abs"]):
                    by_key[key] = row

    by_base: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in by_key.values():
        by_base[int(row["base_t"])].append(row)
    for rows in by_base.values():
        rows.sort(key=lambda row: (
            -int((row.get("parameters") or {}).get("distinct_subfield_structures", 0)),
            -int((row.get("parameters") or {}).get("factor_count", 0)),
            int(row["field_disc_abs"]),
            str(row["candidate_hash"]),
        ))
    bases = sorted(by_base, key=lambda base_t: (-opportunity.get(base_t, 0.0), base_t))
    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < int(limit):
        added = False
        for base_t in bases:
            rows = by_base[base_t]
            if depth < len(rows):
                selected.append(rows[depth])
                added = True
                if len(selected) == int(limit):
                    break
        if not added:
            break
        depth += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as handle:
        for row in selected:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temporary = Path(handle.name)
    temporary.replace(output)
    return {
        "sources": [str(path) for path in sources],
        "output": str(output),
        "input_rows": input_rows,
        "valid_rows": valid_rows,
        "committed_rows": committed,
        "structural_keys": len(by_key),
        "selected_rows": len(selected),
        "selected_bases": len({int(row["base_t"]) for row in selected}),
        "selected_opportunity_ceiling": sum(
            opportunity.get(int(row["base_t"]), 0.0) for row in selected
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(select_subfield_products(
        args.sources,
        args.output,
        atlas_path=args.atlas,
        db_path=args.db,
        limit=args.limit,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
