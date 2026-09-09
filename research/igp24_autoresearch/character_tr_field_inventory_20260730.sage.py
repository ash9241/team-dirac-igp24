#!/usr/bin/env sage
"""Inventory totally-real degree-12 quotient fields for selected character lanes.

The quotient action is read from the exact local 24T block-system catalog.
Accepted scoreable even rows q(x^2) are then filtered by q having twelve real
roots and deduplicated with PARI polredabs.  This is an inventory only: no
character search, network call, staging, or submission is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
DEFAULT_OUTPUT = DATA / "character_tr_field_inventory_20260730.jsonl"
DEFAULT_SUMMARY = DATA / "character_tr_field_inventory_20260730_summary.json"
QUOTIENTS = (156, 210, 214, 243, 267)


def canonical_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial)


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def quotient_labels(structures: Path) -> dict[int, set[str]]:
    result = {value: set() for value in QUOTIENTS}
    for line in structures.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        for system in row.get("blockSystems", []):
            action = str(system.get("quotientActionLabel", ""))
            if (
                system.get("shape") == "12x2"
                and action.startswith("12T")
                and int(action[3:]) in result
            ):
                result[int(action[3:])].add(str(row["label"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--structures", type=Path, default=DEFAULT_STRUCTURES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite character field inventory")

    label_map = quotient_labels(args.structures)
    reverse = defaultdict(set)
    for quotient_t, labels in label_map.items():
        for label in labels:
            reverse[label].add(quotient_t)

    all_labels = sorted(reverse)
    placeholders = ",".join("?" for _label in all_labels)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        source_rows = connection.execute(
            f"""
            SELECT v.label,v.r,v.submission_id,v.polynomial_index,
                   p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.scoreable=1
              AND v.label IN ({placeholders})
            ORDER BY length(p.coefficients),p.coefficient_hash
            """,
            tuple(all_labels),
        ).fetchall()
    finally:
        connection.close()

    ring = PolynomialRing(QQ, "y")
    fields = {}
    counts = defaultdict(int)
    for label, real_roots, submission_id, polynomial_index, digest, text in source_rows:
        coefficients = [ZZ(value) for value in str(text).split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            continue
        counts["acceptedEvenIrreducible"] += 1
        if int(quotient.number_of_real_roots()) != 12:
            continue
        counts["totallyRealSourceRows"] += 1
        reduced = ring(pari(quotient).polredabs())
        line = canonical_line(reduced)
        field_hash = hashlib.sha256(line.encode("ascii")).hexdigest()
        for quotient_t in sorted(reverse[str(label)]):
            key = (quotient_t, field_hash)
            item = fields.setdefault(
                key,
                {
                    "canonicalPolynomial": line,
                    "fieldCanonicalSha256": field_hash,
                    "quotientT12": quotient_t,
                    "sourceRows": [],
                },
            )
            item["sourceRows"].append(
                {
                    "coefficientSha256": str(digest),
                    "label": str(label),
                    "polynomialIndex": int(polynomial_index),
                    "r": int(real_roots),
                    "submissionId": str(submission_id),
                }
            )

    rows = [fields[key] for key in sorted(fields)]
    for row in rows:
        row["sourceLabels"] = sorted(
            {source["label"] for source in row["sourceRows"]}
        )
        row["sourceSignatures"] = sorted(
            {source["r"] for source in row["sourceRows"]}
        )
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )
    write_atomic(args.output.resolve(), rendered)
    summary = {
        "acceptedEvenIrreducibleRows": counts["acceptedEvenIrreducible"],
        "canonicalFields": len(rows),
        "canonicalFieldsByQuotient": {
            str(quotient_t): sum(
                row["quotientT12"] == quotient_t for row in rows
            )
            for quotient_t in QUOTIENTS
        },
        "networkCalls": 0,
        "output": str(args.output.resolve()),
        "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "sourceLabelsByQuotient": {
            str(quotient_t): sorted(label_map[quotient_t])
            for quotient_t in QUOTIENTS
        },
        "submissionCalls": 0,
        "totallyRealSourceRows": counts["totallyRealSourceRows"],
    }
    write_atomic(
        args.summary.resolve(),
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
