#!/usr/bin/env python3
"""Select one pilot from each unsubmitted unit-sign sibling pair.

For a fixed source field, subfield structure, and unit mask, the two unit
signs frequently have the same server group while changing the real-root
signature.  Submitting both signs in the same exploratory batch wastes that
calibration signal.  This selector reserves one sign as an exact follow-up by
choosing at most one pilot from every otherwise untouched sign pair.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter, defaultdict
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


def _pair_key(row: dict[str, Any]) -> tuple[Any, ...]:
    parameters = row.get("parameters") or {}
    return (
        str(row.get("source_field_label") or ""),
        int(row["base_t"]),
        int(parameters.get("subfield_degree", 0)),
        int(parameters.get("subfield_index", 0)),
        int(parameters.get("subfield_shift", 0)),
        int(parameters.get("unit_mask", 0)),
    )


def _sign(row: dict[str, Any]) -> int:
    return int((row.get("parameters") or {}).get("unit_sign", 1))


def select_unit_sign_pilots(
    sources: Sequence[Path],
    output: Path,
    *,
    db_path: str | Path = DEFAULT_DB,
    limit: int = 1000,
) -> dict[str, Any]:
    if not 1 <= int(limit) <= 1000:
        raise ValueError("limit must be in [1, 1000]")

    counts: Counter[str] = Counter()
    by_hash: dict[str, dict[str, Any]] = {}
    with Ledger(db_path) as ledger:
        for source in sources:
            counts["source_files"] += 1
            for row in _load_jsonl(source):
                counts["input_rows"] += 1
                record = validate_validity_candidate(row)
                counts["valid_rows"] += 1
                if ledger.candidate_committed(record.candidate_hash):
                    counts["committed_rows"] += 1
                    continue
                previous = by_hash.get(record.candidate_hash)
                if previous is None or int(row["field_disc_abs"]) < int(
                    previous["field_disc_abs"]
                ):
                    by_hash[record.candidate_hash] = row

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in by_hash.values():
        groups[_pair_key(row)].append(row)

    eligible: list[tuple[tuple[Any, ...], list[dict[str, Any]]]] = []
    for key, rows in groups.items():
        signs = {_sign(row) for row in rows}
        if len(signs) < 2:
            counts["rejected_without_fresh_sign_sibling"] += len(rows)
            continue
        rows.sort(key=lambda row: (
            int(row["field_disc_abs"]),
            int(row["target_r"]),
            -_sign(row),
            str(row["candidate_hash"]),
        ))
        eligible.append((key, rows))

    selected: list[dict[str, Any]] = []
    field_use: Counter[str] = Counter()
    base_use: Counter[int] = Counter()
    structure_use: Counter[tuple[int, int, int, int]] = Counter()
    root_use: Counter[int] = Counter()
    remaining = eligible[:]
    while remaining and len(selected) < int(limit):
        best_index = min(
            range(len(remaining)),
            key=lambda index: _rank_group(
                remaining[index],
                field_use=field_use,
                base_use=base_use,
                structure_use=structure_use,
                root_use=root_use,
            ),
        )
        key, rows = remaining.pop(best_index)
        field, base_t, degree, subfield_index, shift, _ = key
        structure = (base_t, degree, subfield_index, shift)
        row = min(
            rows,
            key=lambda candidate: (
                root_use[int(candidate["target_r"])],
                int(candidate["field_disc_abs"]),
                -_sign(candidate),
                str(candidate["candidate_hash"]),
            ),
        )
        selected.append(row)
        field_use[field] += 1
        base_use[base_t] += 1
        structure_use[structure] += 1
        root_use[int(row["target_r"])] += 1

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
        "counts": dict(sorted(counts.items())),
        "eligible_sign_pairs": len(eligible),
        "selected_pilots": len(selected),
        "reserved_siblings": len(selected),
        "selected_fields": len(field_use),
        "selected_bases": len(base_use),
        "selected_structures": len(structure_use),
        "selected_roots": len(root_use),
        "root_use": dict(sorted(root_use.items())),
    }


def _rank_group(
    group: tuple[tuple[Any, ...], list[dict[str, Any]]],
    *,
    field_use: Counter[str],
    base_use: Counter[int],
    structure_use: Counter[tuple[int, int, int, int]],
    root_use: Counter[int],
) -> tuple[Any, ...]:
    key, rows = group
    field, base_t, degree, subfield_index, shift, mask = key
    structure = (base_t, degree, subfield_index, shift)
    best_root_use = min(root_use[int(row["target_r"])] for row in rows)
    return (
        field_use[field],
        base_use[base_t],
        structure_use[structure],
        best_root_use,
        min(int(row["field_disc_abs"]) for row in rows),
        int(mask),
        key,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(select_unit_sign_pilots(
        args.sources,
        args.output,
        db_path=args.db,
        limit=args.limit,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
