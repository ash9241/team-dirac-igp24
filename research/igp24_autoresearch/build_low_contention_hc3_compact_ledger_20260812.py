#!/usr/bin/env python3
"""Build a four-source read-only ledger for the refreshed hc3 exact wave."""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "low_contention_hc3_compact_ledger_20260812.sqlite3"
KEYS = (
    ("sub_4870cbf5543d4da1ae4e86d0934c7fc2", 1),
    ("sub_4870cbf5543d4da1ae4e86d0934c7fc2", 2),
    ("sub_5479f13430eb40a5aa35612f776c8d81", 0),
    ("sub_a1e6533017bd44d8b514fe248411365b", 1),
)


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    source = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    output = sqlite3.connect(OUTPUT)
    output.executescript(
        """
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
          label TEXT NOT NULL,
          t INTEGER NOT NULL,
          r INTEGER NOT NULL,
          status TEXT NOT NULL,
          scoreable INTEGER NOT NULL,
          PRIMARY KEY(submission_id,polynomial_index)
        );
        """
    )
    for key in KEYS:
        polynomial = source.execute(
            "SELECT submission_id,polynomial_index,coefficients,coefficient_hash "
            "FROM polynomials WHERE submission_id=? AND polynomial_index=?", key
        ).fetchone()
        verification = source.execute(
            "SELECT submission_id,polynomial_index,label,t,r,status,scoreable "
            "FROM verifications WHERE submission_id=? AND polynomial_index=?", key
        ).fetchone()
        if polynomial is None or verification is None:
            raise ValueError(f"missing source {key}")
        output.execute(
            "INSERT INTO polynomials VALUES(?,?,?,?)", tuple(polynomial)
        )
        output.execute(
            "INSERT INTO verifications VALUES(?,?,?,?,?,?,?)", tuple(verification)
        )
    output.commit()
    if output.execute("SELECT count(*) FROM polynomials").fetchone()[0] != 4:
        raise ValueError("compact ledger row-count mismatch")
    output.close()
    source.close()
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
