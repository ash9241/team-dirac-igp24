#!/usr/bin/env python3
"""Select novelty-first validity batches from unit-twisted Kummer fields.

The original unit-twist selector minimizes discriminant.  That is useful once
the resulting group is known, but an exploration batch can otherwise spend
hundreds of slots on the same field/character shape.  This selector balances
source fields, subfield structures, real-root signatures, unit-mask weights,
and masks before using discriminant as a tie breaker.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.submit_exploration_batch import validate_validity_candidate


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _features(row: dict[str, Any]) -> tuple[Any, ...]:
    parameters = row.get("parameters") or {}
    mask = int(parameters.get("unit_mask", 0))
    structure = (
        int(row["base_t"]),
        int(parameters.get("subfield_degree", 0)),
        int(parameters.get("subfield_index", 0)),
        int(parameters.get("subfield_shift", 0)),
    )
    return (
        str(row.get("source_field_label") or ""),
        structure,
        int(row["target_r"]),
        bin(abs(int(mask))).count("1"),
        mask,
        int(parameters.get("unit_sign", 1)),
    )


def select_diverse_unit_twists(
    sources: Sequence[Path],
    output: Path,
    *,
    db_path: str | Path = DEFAULT_DB,
    limit: int = 1000,
) -> dict[str, Any]:
    if not 1 <= int(limit) <= 1000:
        raise ValueError("limit must be in [1, 1000]")

    by_hash: dict[str, dict[str, Any]] = {}
    input_rows = valid_rows = committed_rows = duplicate_rows = 0
    with Ledger(db_path) as ledger:
        for source in sources:
            for row in _load_jsonl(source):
                input_rows += 1
                record = validate_validity_candidate(row)
                valid_rows += 1
                if ledger.candidate_committed(record.candidate_hash):
                    committed_rows += 1
                    continue
                previous = by_hash.get(record.candidate_hash)
                if previous is not None:
                    duplicate_rows += 1
                if previous is None or int(row["field_disc_abs"]) < int(
                    previous["field_disc_abs"]
                ):
                    by_hash[record.candidate_hash] = row

    remaining = list(by_hash.values())
    selected: list[dict[str, Any]] = []
    field_use: Counter[str] = Counter()
    structure_use: Counter[tuple[Any, ...]] = Counter()
    root_use: Counter[int] = Counter()
    weight_use: Counter[tuple[tuple[Any, ...], int]] = Counter()
    mask_use: Counter[tuple[tuple[Any, ...], int]] = Counter()
    sign_use: Counter[tuple[tuple[Any, ...], int]] = Counter()

    while remaining and len(selected) < int(limit):
        best_index = min(
            range(len(remaining)),
            key=lambda index: _rank(
                remaining[index],
                field_use=field_use,
                structure_use=structure_use,
                root_use=root_use,
                weight_use=weight_use,
                mask_use=mask_use,
                sign_use=sign_use,
            ),
        )
        row = remaining.pop(best_index)
        field, structure, root, weight, mask, sign = _features(row)
        selected.append(row)
        field_use[field] += 1
        structure_use[structure] += 1
        root_use[root] += 1
        weight_use[(structure, weight)] += 1
        mask_use[(structure, mask)] += 1
        sign_use[(structure, sign)] += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False
    ) as handle:
        for row in selected:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temporary = Path(handle.name)
    temporary.replace(output)

    return {
        "sources": [str(path) for path in sources],
        "output": str(output),
        "input_rows": input_rows,
        "valid_rows": valid_rows,
        "committed_rows": committed_rows,
        "duplicate_rows": duplicate_rows,
        "eligible_rows": len(by_hash),
        "selected_rows": len(selected),
        "selected_fields": len(field_use),
        "selected_structures": len(structure_use),
        "selected_roots": len(root_use),
        "selected_masks": len({
            (structure, mask) for structure, mask in mask_use
        }),
        "field_use_max": max(field_use.values(), default=0),
        "structure_use_histogram": dict(sorted(
            Counter(structure_use.values()).items()
        )),
        "root_use": dict(sorted(root_use.items())),
    }


def _rank(
    row: dict[str, Any],
    *,
    field_use: Counter[str],
    structure_use: Counter[tuple[Any, ...]],
    root_use: Counter[int],
    weight_use: Counter[tuple[tuple[Any, ...], int]],
    mask_use: Counter[tuple[tuple[Any, ...], int]],
    sign_use: Counter[tuple[tuple[Any, ...], int]],
) -> tuple[Any, ...]:
    field, structure, root, weight, mask, sign = _features(row)
    return (
        field_use[field],
        structure_use[structure],
        root_use[root],
        weight_use[(structure, weight)],
        mask_use[(structure, mask)],
        sign_use[(structure, sign)],
        int(row["field_disc_abs"]),
        str(row["candidate_hash"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(select_diverse_unit_twists(
        args.sources,
        args.output,
        db_path=args.db,
        limit=args.limit,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
