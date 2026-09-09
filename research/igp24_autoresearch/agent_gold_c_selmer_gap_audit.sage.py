#!/usr/bin/env sage -python
"""Audit squareclasses omitted by the S-unit character-kernel search.

The existing pilot searches O_{K,S}^*/squares.  The exact K(S,2) Selmer
space can be strictly larger by the 2-torsion of the S-class group.  This
offline audit compares the two GF(2) spaces for frozen source/support pairs
and emits explicit representatives outside the S-unit span.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import GF, Matrix, NumberField, PolynomialRing, QQ, ZZ, prod


ROOT = Path(__file__).resolve().parent


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def quotient_from_db(db: Path, submission_id: str, polynomial_index: int):
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT coefficients,coefficient_hash FROM polynomials "
            "WHERE submission_id=? AND polynomial_index=?",
            (submission_id, polynomial_index),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("source polynomial is missing")
    coefficients = [ZZ(value) for value in str(row[0]).split(",")]
    if len(coefficients) != 25 or any(coefficients[index] for index in range(1, 25, 2)):
        raise ValueError("source is not an even monic degree-24 polynomial")
    quotient = PolynomialRing(QQ, "y")(coefficients[::2])
    if not quotient.is_irreducible() or quotient.number_of_real_roots() != 12:
        raise ValueError("quotient is not an irreducible totally real degree-12 field")
    return quotient, str(row[1])


def audit_source(db: Path, source_text: str, support_text: str) -> dict:
    submission_id, index_text = source_text.rsplit(":", 1)
    rational_primes = sorted({int(value) for value in support_text.split(",") if value})
    if not rational_primes:
        raise ValueError("support must contain at least one rational prime")
    quotient, coefficient_hash = quotient_from_db(
        db, submission_id, int(index_text)
    )
    field = NumberField(quotient, "a")
    prime_ideals = [
        prime_ideal
        for prime in rational_primes
        for prime_ideal in field.primes_above(prime)
    ]
    selmer, selmer_generators, from_selmer, to_selmer = field.selmer_space(
        prime_ideals, ZZ(2), proof=False
    )
    s_units = field.S_unit_group(proof=False, S=prod(rational_primes))
    s_unit_generators = list(s_units.gens_values())
    s_unit_vectors = [to_selmer(value) for value in s_unit_generators]
    s_unit_span = selmer.subspace(s_unit_vectors)
    extra_basis = []
    enlarged = s_unit_span
    for basis_vector in selmer.basis():
        if basis_vector in enlarged:
            continue
        extra_basis.append(basis_vector)
        enlarged = selmer.subspace([*enlarged.basis(), basis_vector])
    representatives = []
    support_set = set(prime_ideals)
    for vector in extra_basis:
        representative = from_selmer(vector)
        ideal_factors = list(field.ideal(representative).factor())
        outside_even = all(
            ideal in support_set or int(exponent) % 2 == 0
            for ideal, exponent in ideal_factors
        )
        representatives.append(
            {
                "exactIdealFactorization": [
                    {"exponent": int(exponent), "primeIdeal": str(ideal)}
                    for ideal, exponent in ideal_factors
                ],
                "outsideSupportValuationsEven": outside_even,
                "rationalNorm": str(representative.norm()),
                "representative": str(representative),
                "selmerVector": [int(value) for value in vector],
            }
        )
    return {
        "fieldDiscriminantAbs": str(abs(ZZ(field.discriminant()))),
        "primeIdealSupportCount": len(prime_ideals),
        "rationalPrimeSupport": rational_primes,
        "selmerDimension": int(selmer.dimension()),
        "selmerGeneratorCount": len(selmer_generators),
        "source": {
            "coefficientSha256": coefficient_hash,
            "polynomialIndex": int(index_text),
            "submissionId": submission_id,
        },
        "strictExtraDimension": int(selmer.dimension() - s_unit_span.dimension()),
        "strictExtraRepresentatives": representatives,
        "sUnitGeneratorCount": len(s_unit_generators),
        "sUnitSpanDimension": int(s_unit_span.dimension()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--support", action="append", default=[])
    parser.add_argument("--audit-bank", action="append", type=Path, default=[])
    parser.add_argument("--maximum-fields", type=int, default=1000000)
    parser.add_argument("--stop-on-strict", action="store_true")
    parser.add_argument("--prior-bank", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.source) != len(args.support):
        raise ValueError("--source and --support counts must match")
    if args.output.exists():
        raise ValueError("refusing to overwrite Selmer gap audit")
    work = [(source, support, None) for source, support in zip(args.source, args.support)]
    seen_fields = set()
    for bank in args.audit_bank:
        with bank.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                bank_row = json.loads(line)
                for attempt in bank_row.get("attempts", []):
                    field_key = str(attempt["fieldCanonicalSha256"])
                    if field_key in seen_fields:
                        continue
                    seen_fields.add(field_key)
                    identity = attempt["identity"]
                    support = sorted(
                        {
                            *[int(value) for value in ZZ(identity[4]).prime_divisors()],
                            *[int(value) for value in identity[5]],
                        }
                    )
                    work.append(
                        (
                            f"{identity[2]}:{int(identity[3])}",
                            ",".join(str(value) for value in support),
                            field_key,
                        )
                    )
    if not work:
        raise ValueError("no Selmer audit sources were selected")
    rows = []
    for source, support, field_key in work[: args.maximum_fields]:
        try:
            row = audit_source(args.db, source, support)
            row["fieldCanonicalSha256"] = field_key
        except Exception as exc:
            row = {
                "error": f"{type(exc).__name__}: {exc}",
                "fieldCanonicalSha256": field_key,
                "sourceText": source,
                "supportText": support,
            }
        rows.append(row)
        print(
            json.dumps(
                {
                    "audited": len(rows),
                    "event": "field_audited",
                    "fieldCanonicalSha256": field_key,
                    "strictExtraDimension": row.get("strictExtraDimension"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if args.stop_on_strict and row.get("strictExtraDimension", 0) > 0:
            break
    prior_banks = [
        {"path": str(path.resolve()), "sha256": sha256_path(path)}
        for path in args.prior_bank
    ]
    payload = {
        "audit": rows,
        "existingMechanism": "O_{K,S}^*/(O_{K,S}^*)^2 S-unit squareclasses",
        "genuinelyNewMechanism": (
            "exact K(S,2) Selmer squareclasses, adjoining S-class-group "
            "2-torsion representatives outside the S-unit span"
        ),
        "networkCalls": 0,
        "priorBanks": prior_banks,
        "strictCoverageCertified": any(
            row.get("strictExtraDimension", 0) > 0 for row in rows
        ),
        "submissionCalls": 0,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "strictCoverageCertified": payload["strictCoverageCertified"],
                "strictExtraDimensions": [
                    row.get("strictExtraDimension") for row in rows
                ],
            },
            sort_keys=True,
        )
    )
    return 0 if payload["strictCoverageCertified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
