#!/usr/bin/env python3
"""Stage novel degree-24 fields from the completed D4-over-6T11 pilot.

The pilot rows are exact irreducible degree-24 polynomials.  Their cycle
profiles make them high-value candidates for the live D4/6T11 target family,
but this stager deliberately makes no claim that Frobenius compatibility is
an exact group identification.  Existing ledger and outbox hashes are
excluded before a new portfolio is written.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "current_d4_6t11_full_wave1_20260812.jsonl"
OUTPUT = ROOT / "outbox" / "current_d4_6t11_hybrid_20260813.txt"
CERTIFICATE = ROOT / "data" / "current_d4_6t11_hybrid_20260813_certificate.json"


def digest(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def main() -> int:
    connection = sqlite3.connect(ROOT / "data" / "ledger.sqlite3")
    try:
        known = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    finally:
        connection.close()

    outbox_known: set[str] = set()
    for path in (ROOT / "outbox").glob("*.txt"):
        if path == OUTPUT:
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line:
                outbox_known.add(digest(line))

    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        line = str(row["polynomial"])
        row_hash = digest(line)
        if row_hash != row["coefficientSha256"]:
            raise ValueError(f"source hash mismatch: {row_hash}")
        coefficients = [int(value) for value in line.split(",")]
        if len(coefficients) != 25 or coefficients[-1] != 1:
            raise ValueError(f"source is not monic degree 24: {row_hash}")
        if row_hash in known or row_hash in outbox_known or row_hash in seen:
            continue
        seen.add(row_hash)
        selected.append(row)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("".join(f"{row['polynomial']}\n" for row in selected), encoding="utf-8")
    payload = {
        "campaign": "current-d4-6t11-hybrid",
        "exactClaims": ["degree=24", "irreducible", "real-root count from source pilot"],
        "heuristicClaims": ["D4/6T11 structural family", "Frobenius-compatible with live gold catalog"],
        "excludedInLedger": sum(row["coefficientSha256"] in known for row in rows),
        "excludedInOutbox": sum(row["coefficientSha256"] in outbox_known for row in rows),
        "selected": len(selected),
        "source": str(SOURCE.resolve()),
        "sourceRows": len(rows),
        "uniqueGoldCatalogMatches": sum(len(row["compatibleGoldLabels"]) == 1 for row in selected),
        "output": str(OUTPUT.resolve()),
        "outputSha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    CERTIFICATE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
