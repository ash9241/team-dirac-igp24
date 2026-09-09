#!/usr/bin/env python3
"""Build a minimal, provenance-checked source database for an index-24 plan."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")

    sources: dict[tuple[str, int], dict] = {}
    with args.plan.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = (str(row["sourceSubmissionId"]), int(row["sourcePolynomialIndex"]))
            expected = {
                "coefficient_hash": str(row["coefficientSha256"]),
                "label": str(row["sourceLabel"]),
                "r": int(row["sourceR"]),
            }
            prior = sources.setdefault(key, expected)
            if prior != expected:
                raise ValueError(f"conflicting source provenance for {key}")

    source = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    output = sqlite3.connect(args.output)
    try:
        output.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE polynomials(
              submission_id TEXT NOT NULL,
              polynomial_index INTEGER NOT NULL,
              coefficients TEXT NOT NULL,
              coefficient_hash TEXT NOT NULL,
              PRIMARY KEY(submission_id,polynomial_index)
            );
            CREATE TABLE verifications(
              submission_id TEXT NOT NULL,
              polynomial_index INTEGER NOT NULL,
              label TEXT,
              r INTEGER,
              status TEXT,
              PRIMARY KEY(submission_id,polynomial_index)
            );
            CREATE INDEX p_hash ON polynomials(coefficient_hash);
            """
        )
        for key, expected in sources.items():
            row = source.execute(
                """
                SELECT p.submission_id,p.polynomial_index,p.coefficients,p.coefficient_hash,
                       v.label,v.r,v.status
                FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
                WHERE p.submission_id=? AND p.polynomial_index=?
                """,
                key,
            ).fetchone()
            if row is None:
                raise ValueError(f"missing source {key}")
            if (
                str(row[3]) != expected["coefficient_hash"]
                or str(row[4]) != expected["label"]
                or int(row[5]) != expected["r"]
                or str(row[6]) != "accepted"
            ):
                raise ValueError(f"source provenance mismatch for {key}")
            output.execute("INSERT INTO polynomials VALUES(?,?,?,?)", row[:4])
            output.execute("INSERT INTO verifications VALUES(?,?,?,?,?)", (row[0],row[1],row[4],row[5],row[6]))
        output.commit()
        integrity = output.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity check failed: {integrity}")
        counts = {
            "distinctSources": len(sources),
            "polynomials": output.execute("SELECT COUNT(*) FROM polynomials").fetchone()[0],
            "verifications": output.execute("SELECT COUNT(*) FROM verifications").fetchone()[0],
        }
    except Exception:
        output.close()
        source.close()
        if args.output.exists():
            args.output.unlink()
        raise
    output.close()
    source.close()
    print(json.dumps({"output": str(args.output), **counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
