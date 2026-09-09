#!/usr/bin/env python3
"""Build a compact source ledger for certified pair-action runbooks."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "ledger.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser()
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--certificate", type=Path)
    inputs.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output_path = args.output.resolve()
    if output_path.exists():
        raise FileExistsError(output_path)
    input_path = args.certificate or args.plan
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    rows = payload.get("runbooks", []) if args.certificate else payload.get("groups", [])
    keys = sorted({
        (str(row["source"]["submissionId"]), int(row["source"]["polynomialIndex"]))
        for row in rows
    })
    if not keys:
        raise ValueError("input contains no source rows")
    source = sqlite3.connect(f"file:{SOURCE.resolve()}?mode=ro", uri=True)
    output = sqlite3.connect(output_path)
    try:
        output.executescript(
            """
            CREATE TABLE polynomials(
              submission_id TEXT NOT NULL,polynomial_index INTEGER NOT NULL,
              coefficients TEXT NOT NULL,coefficient_hash TEXT NOT NULL,
              PRIMARY KEY(submission_id,polynomial_index));
            CREATE TABLE verifications(
              submission_id TEXT NOT NULL,polynomial_index INTEGER NOT NULL,
              label TEXT NOT NULL,t INTEGER NOT NULL,r INTEGER NOT NULL,
              status TEXT NOT NULL,scoreable INTEGER NOT NULL,
              PRIMARY KEY(submission_id,polynomial_index));
            """
        )
        for key in keys:
            polynomial = source.execute(
                "SELECT submission_id,polynomial_index,coefficients,coefficient_hash "
                "FROM polynomials WHERE submission_id=? AND polynomial_index=?", key,
            ).fetchone()
            verification = source.execute(
                "SELECT submission_id,polynomial_index,label,t,r,status,scoreable "
                "FROM verifications WHERE submission_id=? AND polynomial_index=?", key,
            ).fetchone()
            if polynomial is None or verification is None:
                raise ValueError(f"missing source {key}")
            output.execute("INSERT INTO polynomials VALUES(?,?,?,?)", polynomial)
            output.execute("INSERT INTO verifications VALUES(?,?,?,?,?,?,?)", verification)
        output.commit()
        print(json.dumps({"keys": len(keys), "output": str(output_path)}))
    finally:
        output.close()
        source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
