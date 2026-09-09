#!/usr/bin/env python3
"""Build endpoint-signature character jobs from local GaloisDB degree-12 fields."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--base-t", type=int, required=True)
    parser.add_argument("--target-t", type=int, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-team-count", type=int, default=3)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        owned = {
            int(root)
            for (root,) in connection.execute(
                "SELECT DISTINCT r FROM verifications WHERE label=? "
                "AND status='accepted'",
                (f"24T{args.target_t}",),
            )
        }
        roots = []
        target_rows = {}
        for root in (0, 24):
            row = connection.execute(
                "SELECT team_count,minimum_disc_abs,generated_at FROM targets "
                "WHERE label=? AND r=?",
                (f"24T{args.target_t}", root),
            ).fetchone()
            baseline = connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
                (f"24T{args.target_t}", root),
            ).fetchone()
            if (
                row is not None
                and int(row[0]) <= args.max_team_count
                and root not in owned
                and baseline is None
            ):
                roots.append(root)
                target_rows[str(root)] = {
                    "teamCount": int(row[0]),
                    "minimumDiscAbs": str(row[1]) if row[1] is not None else None,
                    "generatedAt": str(row[2]) if row[2] is not None else None,
                }
    finally:
        connection.close()
    if not roots:
        raise ValueError("no live endpoint target remains")

    rows = []
    seen = set()
    for line in args.catalog.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if int(row.get("base_t", -1)) != args.base_t:
            continue
        if int(row.get("source_signature", -1)) != 12:
            continue
        coefficients = tuple(int(value) for value in row["coefficients"])
        if coefficients in seen:
            continue
        seen.add(coefficients)
        digest = hashlib.sha256(
            ",".join(map(str, coefficients)).encode("ascii")
        ).hexdigest()
        rows.append(
            {
                "baseCoefficients": list(coefficients),
                "baseT": args.base_t,
                "catalogLabel": str(row["label"]),
                "jobId": digest[:20],
                "requestedRoots": roots,
                "targetRows": target_rows,
                "targetT": args.target_t,
            }
        )
        if args.limit > 0 and len(rows) >= args.limit:
            break
    for index, row in enumerate(rows):
        row["jobIndex"] = index
    rendered = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    summary = {
        "baseT": args.base_t,
        "targetT": args.target_t,
        "requestedRoots": roots,
        "jobs": len(rows),
        "valueCeiling": sum(2.0 ** (-target_rows[str(root)]["teamCount"]) for root in roots),
        "output": str(args.output.resolve()),
        "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
