#!/usr/bin/env python3
"""Build a compact target-state ledger for one current squareclass lane."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "ledger.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--quotient-t", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output_path = args.output.resolve()
    if output_path.exists():
        raise FileExistsError(output_path)
    payload = json.loads(args.sources.read_text(encoding="utf-8"))
    lanes = [
        lane for lane in payload.get("lanes", [])
        if int(lane.get("quotientT12", -1)) == args.quotient_t
    ]
    if len(lanes) != 1:
        raise ValueError(f"expected one q{args.quotient_t} lane, found {len(lanes)}")
    labels = sorted({str(row["label"]) for row in lanes[0].get("currentGoldTargets", [])})
    if not labels:
        raise ValueError("lane has no target labels")
    placeholders = ",".join("?" for _ in labels)

    source = sqlite3.connect(f"file:{SOURCE.resolve()}?mode=ro", uri=True)
    output = sqlite3.connect(output_path)
    try:
        output.executescript(
            """
            CREATE TABLE targets(
              label TEXT NOT NULL,t INTEGER NOT NULL,r INTEGER NOT NULL,
              team_count INTEGER NOT NULL,minimum_disc_abs TEXT,
              discovered INTEGER NOT NULL,generated_at TEXT,
              PRIMARY KEY(label,r));
            CREATE TABLE baseline_pairs(
              label TEXT NOT NULL,r INTEGER NOT NULL,best_nfdisc_abs TEXT NOT NULL,
              source_rows INTEGER NOT NULL,PRIMARY KEY(label,r));
            CREATE TABLE verifications(
              submission_id TEXT NOT NULL,polynomial_index INTEGER NOT NULL,
              status TEXT,label TEXT,t INTEGER,r INTEGER,scoring_status TEXT,
              scoreable INTEGER,in_baseline INTEGER,baseline_unlocked INTEGER,
              disc_source TEXT,field_disc_abs TEXT,poly_disc_abs TEXT,
              mixed_disc_abs TEXT,scoring_disc_abs TEXT,raw_json TEXT NOT NULL,
              PRIMARY KEY(submission_id,polynomial_index));
            """
        )
        for table in ("targets", "baseline_pairs", "verifications"):
            rows = source.execute(
                f"SELECT * FROM {table} WHERE label IN ({placeholders})", labels
            ).fetchall()
            if rows:
                marks = ",".join("?" for _ in rows[0])
                output.executemany(f"INSERT INTO {table} VALUES({marks})", rows)
        output.commit()
        print(json.dumps({
            "labels": labels,
            "output": str(output_path),
            "targets": output.execute("SELECT count(*) FROM targets").fetchone()[0],
            "verifications": output.execute("SELECT count(*) FROM verifications").fetchone()[0],
        }))
    finally:
        output.close()
        source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
