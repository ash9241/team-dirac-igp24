#!/usr/bin/env python3
"""Freeze the authoritative locally-unowned, nonbaseline SAIR gold-pair list."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_OUTPUT = ROOT / "data" / "live_undiscovered_signatures.jsonl"
DEFAULT_SUMMARY = ROOT / "data" / "live_undiscovered_signatures_summary.json"


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    arguments = parser.parse_args()

    with sqlite3.connect(f"file:{arguments.db}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT target.t, target.label, target.r, target.team_count,
                   target.minimum_disc_abs, target.generated_at
              FROM targets AS target
             WHERE target.discovered = 0
               AND NOT EXISTS (
                    SELECT 1 FROM baseline_pairs AS baseline
                     WHERE baseline.label = target.label AND baseline.r = target.r
               )
               AND NOT EXISTS (
                    SELECT 1 FROM verifications AS verification
                     WHERE verification.label = target.label
                       AND verification.r = target.r
                       AND verification.scoreable = 1
               )
             ORDER BY target.t, target.r
            """
        ).fetchall()

    frozen_at = datetime.now(timezone.utc).isoformat()
    output_rows = [
        {
            "t": int(row["t"]),
            "label": row["label"],
            "r": int(row["r"]),
            "teamCount": int(row["team_count"]),
            "minimumDiscAbs": row["minimum_disc_abs"],
            "targetGeneratedAt": row["generated_at"],
            "frozenAt": frozen_at,
            "projectedNewGoldPoints": 1.0,
        }
        for row in rows
    ]
    payload = b"".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
        for row in output_rows
    )
    atomic_write(arguments.output, payload)

    signature_counts = Counter(str(row["r"]) for row in output_rows)
    label_counts = Counter(row["label"] for row in output_rows)
    generated_values = sorted({row["targetGeneratedAt"] for row in output_rows})
    summary = {
        "database": str(arguments.db.resolve()),
        "frozenAt": frozen_at,
        "targetGeneratedAtValues": generated_values,
        "rows": len(output_rows),
        "distinctLabels": len(label_counts),
        "signatureCounts": dict(sorted(signature_counts.items(), key=lambda item: int(item[0]))),
        "projectedAllGoldPoints": float(len(output_rows)),
        "output": str(arguments.output.resolve()),
        "outputSha256": hashlib.sha256(payload).hexdigest(),
        "selection": "targets.discovered=0 AND no baseline pair AND no locally scoreable verification",
    }
    atomic_write(
        arguments.summary,
        (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
