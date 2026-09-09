#!/usr/bin/env sage -python
"""Attempt an independent exact Sage/GAP identification of one candidate.

This is a read-only arithmetic worker.  It selects a coefficient hash from a
JSON artifact, verifies the stored hash and degree-24 polynomial, and asks
Sage's GAP Galois-group backend for the transitive identification.  It has no
network, ledger, staging, or submission path; callers should impose an
external wall-clock timeout because degree-24 identification can be hard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ


def candidate_rows(value):
    if isinstance(value, dict):
        if "candidateSha256" in value and "candidateCoefficientLine" in value:
            yield value
        for child in value.values():
            yield from candidate_rows(child)
    elif isinstance(value, list):
        for child in value:
            yield from candidate_rows(child)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--candidate-sha256", required=True)
    args = parser.parse_args()

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    matches = [
        row
        for row in candidate_rows(payload)
        if row.get("candidateSha256") == args.candidate_sha256
    ]
    lines = {str(row["candidateCoefficientLine"]) for row in matches}
    if len(lines) != 1:
        raise ValueError(
            f"candidate hash selected {len(matches)} rows with {len(lines)} lines"
        )
    line = lines.pop()
    if hashlib.sha256(line.encode("ascii")).hexdigest() != args.candidate_sha256:
        raise ValueError("candidate coefficient hash mismatch")
    coefficients = [ZZ(value) for value in line.split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1 or coefficients[0] == 0:
        raise ValueError("candidate is not monic degree 24")

    ring = PolynomialRing(QQ, "x")
    polynomial = ring(coefficients)
    if not polynomial.is_irreducible():
        raise ValueError("candidate is reducible")
    real_roots = int(polynomial.number_of_real_roots())
    print(
        json.dumps(
            {
                "candidateSha256": args.candidate_sha256,
                "event": "degree24_galois_identification_start",
                "realRoots": real_roots,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    group = polynomial.galois_group(algorithm="gap")
    transitive_number = int(group.transitive_number())
    print(
        json.dumps(
            {
                "algorithm": "Sage polynomial.galois_group(algorithm='gap')",
                "candidateSha256": args.candidate_sha256,
                "degree": int(group.degree()),
                "event": "degree24_galois_identification_complete",
                "order": int(group.order()),
                "realRoots": real_roots,
                "transitiveLabel": f"24T{transitive_number}",
                "transitiveNumber": transitive_number,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
