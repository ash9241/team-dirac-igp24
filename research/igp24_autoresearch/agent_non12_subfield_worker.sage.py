#!/usr/bin/env sage -python
"""Recover exact low-degree subfields from owned strict-non-F1 fields."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, ZZ, libgap, pari


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
RING = PolynomialRing(ZZ, "x")


def canonical_line(polynomial) -> str:
    values = [int(value) for value in polynomial.list()]
    if len(values) != polynomial.degree() + 1 or values[-1] != 1:
        raise ValueError("subfield polynomial is not monic integral")
    return ",".join(str(value) for value in values)


def polynomial_line(polynomial) -> str:
    return ",".join(str(int(value)) for value in polynomial.list())


def low_group(polynomial) -> dict:
    degree = int(polynomial.degree())
    galois = pari(polynomial).polgalois()
    order = int(galois[0])
    t = int(galois[2])
    label = f"{degree}T{t}"
    group = libgap.TransitiveGroup(degree, t)
    if int(libgap.Size(group)) != order:
        raise ArithmeticError(f"PARI/GAP group-order mismatch for {label}")
    blocks = list(libgap.AllBlocks(group)) if degree > 1 else []
    proper_sizes = sorted({int(libgap.Length(block)) for block in blocks})
    return {
        "degree": degree,
        "label": label,
        "order": order,
        "pariName": str(galois[3]),
        "isSolvable": bool(libgap.IsSolvableGroup(group)),
        "properBlockSizes": proper_sizes,
        "properSubfieldDegrees": sorted({degree // size for size in proper_sizes}),
    }


def process(task: dict, connection: sqlite3.Connection) -> list[dict]:
    source = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.field_disc_abs,v.scoreable
        FROM polynomials AS p JOIN verifications AS v USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (str(task["submissionId"]), int(task["polynomialIndex"])),
    ).fetchone()
    if source is None or int(source[5] or 0) != 1 or str(source[2]) != str(task["sourceLabel"]):
        raise RuntimeError("source ledger identity/profile mismatch")
    source_polynomial = RING([int(value) for value in str(source[0]).split(",")])
    if source_polynomial.degree() != 24 or not source_polynomial.is_irreducible():
        raise ArithmeticError("source polynomial failed exact degree/irreducibility check")
    rows = []
    for degree in task["degrees"]:
        degree = int(degree)
        subfields = list(pari.nfsubfields(pari(source_polynomial), degree))
        for ordinal, pair in enumerate(subfields):
            defining = RING(pair[0])
            embedding = str(pair[1])
            reduced = RING(pari(defining).polredbest())
            if reduced.degree() != degree or not reduced.is_monic() or not reduced.is_irreducible():
                raise ArithmeticError("recovered subfield failed exact polynomial checks")
            line = canonical_line(reduced)
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            group = low_group(reduced)
            if not group["isSolvable"]:
                raise ArithmeticError("subfield of a solvable source unexpectedly nonsolvable")
            rows.append(
                {
                    "status": "certified_recoverable_subfield",
                    "sourceLabel": str(source[2]),
                    "sourceR": int(source[3]),
                    "sourceFieldDiscriminantAbs": str(source[4]) if source[4] else None,
                    "sourceSubmissionId": str(task["submissionId"]),
                    "sourcePolynomialIndex": int(task["polynomialIndex"]),
                    "sourceCoefficientSha256": str(source[1]),
                    "sourceHas12x2BlockSystem": False,
                    "subfieldDegree": degree,
                    "subfieldOrdinal": ordinal,
                    "definingPolynomialBeforeReduction": canonical_line(defining),
                    "embeddingGeneratorInSource": embedding,
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "coefficientBytes": len(line.encode("ascii")),
                    "realRoots": int(reduced.number_of_real_roots()),
                    "fieldDiscriminantAbs": str(abs(int(pari(reduced).nfdisc()))),
                    "polynomialDiscriminantAbs": str(abs(int(reduced.discriminant()))),
                    "galoisGroup": group,
                    "exactProof": {
                        "method": "PARI nfsubfields plus exact polredbest isomorphism",
                        "sourceDegree": 24,
                        "subfieldDegree": degree,
                    },
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tasks = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    output = []
    for task in tasks:
        output.extend(process(task, connection))
    connection.close()
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in output:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(json.dumps({"tasks": len(tasks), "subfields": len(output), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
