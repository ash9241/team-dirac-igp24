#!/usr/bin/env python3
"""Export locally generated candidates with server-verified target labels."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from routeA.ledger import DEFAULT_DB, Ledger


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def export_verified_seeds(
    sources: Sequence[Path],
    output: Path,
    *,
    targets: Sequence[int],
    db_path: str | Path = DEFAULT_DB,
    limit_per_target: int = 1,
) -> dict[str, Any]:
    if int(limit_per_target) <= 0:
        raise ValueError("limit_per_target must be positive")
    wanted = {int(value) for value in targets}
    if not wanted:
        raise ValueError("at least one verified target is required")
    rows_by_hash: dict[str, dict[str, Any]] = {}
    for source in sources:
        for row in _load_jsonl(source):
            rows_by_hash[str(row["candidate_hash"])] = row
    matches: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with Ledger(db_path) as ledger:
        placeholders = ",".join("?" for _ in wanted)
        query = f"""
            SELECT candidate_hash, verified_t, verified_r, submission_id, verified_at
            FROM verification
            WHERE accepted=1 AND verified_t IN ({placeholders})
            ORDER BY verified_at DESC
        """
        for verification in ledger.connection.execute(query, tuple(sorted(wanted))):
            row = rows_by_hash.get(str(verification["candidate_hash"]))
            if row is None:
                continue
            enriched = dict(row)
            enriched["calibrated_target_t"] = int(verification["verified_t"])
            enriched["calibrated_target_r"] = int(verification["verified_r"])
            enriched["calibration_submission_id"] = str(verification["submission_id"])
            matches[int(verification["verified_t"])].append(enriched)
    selected: list[dict[str, Any]] = []
    for target_t in sorted(wanted):
        rows = matches.get(target_t, [])
        unique = {str(row["candidate_hash"]): row for row in rows}
        ranked = sorted(unique.values(), key=lambda row: (
            int(row.get("field_disc_abs") or row.get("estimated_nfdisc_abs") or 0),
            int(row["calibrated_target_r"]),
            str(row["candidate_hash"]),
        ))
        selected.extend(ranked[: int(limit_per_target)])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as handle:
        for row in selected:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temporary = Path(handle.name)
    temporary.replace(output)
    return {
        "sources": [str(path) for path in sources],
        "output": str(output),
        "requested_targets": len(wanted),
        "matched_targets": len({int(row["calibrated_target_t"]) for row in selected}),
        "selected_rows": len(selected),
        "missing_targets": sorted(wanted - {int(row["calibrated_target_t"]) for row in selected}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--target", action="append", type=int, required=True)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit-per-target", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(export_verified_seeds(
        args.sources,
        args.output,
        targets=args.target,
        db_path=args.db,
        limit_per_target=args.limit_per_target,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
