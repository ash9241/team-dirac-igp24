#!/usr/bin/env python3
"""Export low-discriminant, server-verified degree-24 source fields."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from routeA.ledger import DEFAULT_DB, Ledger, canonical_coefficients


def export_verified_catalog(
    output_path: str | Path,
    *,
    db_path: str | Path = DEFAULT_DB,
    fields_per_group: int = 1,
    prefer_distinct_roots: bool = False,
) -> dict[str, Any]:
    if fields_per_group < 1:
        raise ValueError("fields_per_group must be positive")
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with Ledger(db_path) as ledger:
        rows = ledger.connection.execute('''
            SELECT c.candidate_hash, c.coefficients, v.submission_id,
                   v.verified_t, v.verified_r,
                   COALESCE(v.nfdisc, v.discriminant) AS field_disc_abs
            FROM verification v
            JOIN candidate c USING(candidate_hash)
            WHERE v.accepted=1 AND v.verified_t IS NOT NULL
              AND v.verified_r IS NOT NULL
        ''')
        for row in rows:
            coefficients = tuple(
                int(value)
                for value in canonical_coefficients(row["coefficients"]).split(",")
            )
            if len(coefficients) != 25 or coefficients[-1] == 0:
                continue
            disc = _positive_int(row["field_disc_abs"])
            if disc is None:
                continue
            target_t = int(row["verified_t"])
            grouped[target_t].append({
                "source_t": target_t,
                "source_r": int(row["verified_r"]),
                "coefficients": list(coefficients),
                "disc_abs": disc,
                "candidate_hash": str(row["candidate_hash"]),
                "submission_id": str(row["submission_id"]),
                "evidence": "server-accepted-exact-label",
            })

    output_rows: list[dict[str, Any]] = []
    for target_t, candidates in grouped.items():
        candidates.sort(key=lambda row: (
            int(row["disc_abs"]),
            int(row["source_r"]),
            str(row["candidate_hash"]),
        ))
        output_rows.extend(
            _select_candidates(
                candidates,
                fields_per_group,
                prefer_distinct_roots=prefer_distinct_roots,
            )
        )
    output_rows.sort(key=lambda row: (
        int(row["source_t"]),
        int(row["disc_abs"]),
        str(row["candidate_hash"]),
    ))
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in output_rows
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(output)
    summary = {
        "output": str(output),
        "rows": len(output_rows),
        "source_groups": len({int(row["source_t"]) for row in output_rows}),
        "fields_per_group": int(fields_per_group),
        "prefer_distinct_roots": bool(prefer_distinct_roots),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }
    output.with_suffix(output.suffix + ".report.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _select_candidates(
    candidates: list[dict[str, Any]],
    limit: int,
    *,
    prefer_distinct_roots: bool,
) -> list[dict[str, Any]]:
    if not prefer_distinct_roots:
        return candidates[:limit]
    first_by_root: dict[int, dict[str, Any]] = {}
    for row in candidates:
        first_by_root.setdefault(int(row["source_r"]), row)
    diverse = sorted(first_by_root.values(), key=lambda row: (
        int(row["disc_abs"]),
        int(row["source_r"]),
        str(row["candidate_hash"]),
    ))
    selected = diverse[:limit]
    selected_hashes = {str(row["candidate_hash"]) for row in selected}
    if len(selected) < limit:
        selected.extend(
            row for row in candidates
            if str(row["candidate_hash"]) not in selected_hashes
        )
    return selected[:limit]


def _positive_int(value: Any) -> int | None:
    try:
        result = abs(int(value))
    except (TypeError, ValueError):
        return None
    return result if result > 1 else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--fields-per-group", type=int, default=1)
    parser.add_argument("--prefer-distinct-roots", action="store_true")
    args = parser.parse_args()
    summary = export_verified_catalog(
        args.output,
        db_path=args.db,
        fields_per_group=args.fields_per_group,
        prefer_distinct_roots=args.prefer_distinct_roots,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
