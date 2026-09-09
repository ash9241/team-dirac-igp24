#!/usr/bin/env python3
"""Select diverse validity-only proper-subfield probes by quotient opportunity."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.submit_exploration_batch import validate_validity_candidate


DEFAULT_ATLAS = Path(__file__).resolve().parent / "data" / "target_atlas.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _base_opportunity(atlas_path: Path) -> dict[int, float]:
    values: defaultdict[int, float] = defaultdict(float)
    for row in _load_jsonl(atlas_path):
        if row.get("status") != "architecture_gap":
            continue
        quotients = {
            int(value["quotient_t"])
            for value in row.get("block_quotients", [])
            if int(value.get("block_size", 0)) == 2
            and int(value.get("quotient_degree", 0)) == 12
        }
        for base_t in quotients:
            values[base_t] += float(row.get("score_ceiling") or 0.0)
    return dict(values)


def select_subfield_exploration(
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
    by_hash: dict[str, dict[str, Any]] = {}
    by_key: dict[tuple[int, ...], dict[str, Any]] = {}
    input_rows = valid_rows = committed = duplicate_candidates = 0
    with Ledger(db_path) as ledger:
        for source in sources:
            for row in _load_jsonl(source):
                input_rows += 1
                record = validate_validity_candidate(row)
                valid_rows += 1
                if ledger.candidate_committed(record.candidate_hash):
                    committed += 1
                    continue
                previous = by_hash.get(record.candidate_hash)
                if previous is not None:
                    duplicate_candidates += 1
                    previous_root_count = int(
                        previous.get("local_root_count", previous["target_r"])
                    )
                    if previous_root_count != record.local_root_count:
                        raise ValueError(
                            f"candidate {record.candidate_hash} has conflicting root counts"
                        )
                    if _representative_rank(row, opportunity) < _representative_rank(
                        previous, opportunity
                    ):
                        by_hash[record.candidate_hash] = row
                    continue
                by_hash[record.candidate_hash] = row

    # One polynomial can be reached through several subfield descriptions and
    # therefore carry different predicted labels.  Validity-only submissions
    # need one deterministic representative per polynomial before structural
    # diversity is applied; otherwise the downstream submitter correctly
    # rejects the duplicate hash as conflicting metadata.
    for row in by_hash.values():
        parameters = row.get("parameters") or {}
        key = (
            int(row["base_t"]),
            int(parameters["subfield_degree"]),
            int(parameters["subfield_index"]),
            int(row["norm_squareclass"]),
            int(row["target_r"]),
        )
        previous = by_key.get(key)
        if previous is None or int(row["field_disc_abs"]) < int(
            previous["field_disc_abs"]
        ):
            by_key[key] = row
    ranked = sorted(
        by_key.values(),
        key=lambda row: (
            -float(opportunity.get(int(row["base_t"]), 0.0)),
            int(row["field_disc_abs"]),
            int(row["base_t"]),
            int(row["target_r"]),
            str(row["candidate_hash"]),
        ),
    )
    selected = ranked[: int(limit)]
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
        "duplicate_candidate_rows": duplicate_candidates,
        "unique_candidates": len(by_hash),
        "structural_keys": len(by_key),
        "selected_rows": len(selected),
        "selected_bases": len({int(row["base_t"]) for row in selected}),
        "selected_opportunity_ceiling": sum(
            opportunity.get(int(row["base_t"]), 0.0) for row in selected
        ),
    }


def _representative_rank(
    row: dict[str, Any], opportunity: dict[int, float]
) -> tuple[Any, ...]:
    """Prefer the most valuable deterministic provenance for a polynomial."""

    return (
        -float(opportunity.get(int(row["base_t"]), 0.0)),
        int(row["field_disc_abs"]),
        int(row["base_t"]),
        int(row.get("target_t") or 0),
        int(row["target_r"]),
        json.dumps(row, sort_keys=True),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    summary = select_subfield_exploration(
        args.sources,
        args.output,
        atlas_path=args.atlas,
        db_path=args.db,
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
