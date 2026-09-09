#!/usr/bin/env sage -python
"""Exact singleton-core q191 lifts with block-quotient Frobenius identity.

The accepted degree-24 parent fixes the exhaustive list of its 12x2 block
quotients.  The source polynomial is checked to be exactly Q(x^2), with the
field-census polynomial equal to PARI polredabs(Q).  Squarefree modular factor
degrees of Q then exclude every alternative block quotient before the Selmer
lift is allowed to run.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from sage.all import GF, PolynomialRing, QQ, ZZ, pari, prime_range
from sage.libs.gap.libgap import libgap
from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "broad_structural_character_gate_20260730.sage.py"
STRUCTURES = ROOT / "data" / "agent_non12_tower_structures.jsonl"
LEDGER = ROOT / "data" / "ledger.sqlite3"
QUOTIENT_T = 191
QUOTIENT_ORDER = 768
RING = PolynomialRing(QQ, "z")


def squareclass_compact_ideal_generator(ideal):
    """Return an exact compact principal generator modulo squares."""
    try:
        field = ideal.number_field()
    except AttributeError:
        return ideal.abs()
    bnf = field.pari_bnf(False)
    principal_data = bnf.bnfisprincipal(ideal.pari_hnf(), 5)
    if any(int(value) for value in principal_data[0]):
        raise ValueError("2-Selmer requested a generator of a nonprincipal ideal")
    compact = principal_data[1]
    rows, columns = (int(value) for value in compact.matsize())
    if columns != 2:
        raise ValueError(
            f"unexpected compact factor matrix shape {(rows, columns)}"
        )
    representative = field.one()
    for row in range(rows):
        if int(compact[row, 1]) % 2:
            representative *= field(bnf.nfbasistoalg(compact[row, 0]))
    return representative


def load_driver():
    spec = importlib.util.spec_from_file_location("q191_frobenius_broad", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()
ORIGINAL_LOAD_BASE = DRIVER.load_base


def singleton_core_load_base(quotient_t: int, target_labels: list[str]):
    if int(quotient_t) != QUOTIENT_T:
        raise ValueError("q191 launcher received another quotient")
    base = ORIGINAL_LOAD_BASE(quotient_t, target_labels)
    base.FAMILIES[quotient_t]["ambiguousTargetLabels"] = []
    return base


def group_cycle_profiles(transitive_t: int) -> set[tuple[int, ...]]:
    group = libgap.TransitiveGroup(12, transitive_t)
    points = libgap.eval("[1..12]")
    return {
        tuple(
            sorted(
                int(value)
                for value in libgap.CycleLengths(
                    libgap.Representative(conjugacy_class), points
                )
            )
        )
        for conjugacy_class in libgap.ConjugacyClasses(group)
    }


def cycle_type(polynomial, prime: int) -> tuple[int, ...] | None:
    reduced = polynomial.change_ring(GF(prime))
    if reduced.degree() != 12 or not reduced.is_squarefree():
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in reduced.factor()
            for _ in range(int(exponent))
        )
    )


def structure_rows() -> dict[str, dict]:
    result = {}
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            label = str(row["label"])
            if label in result:
                raise ValueError(f"duplicated structural label {label}")
            result[label] = row
    return result


def verified_even_quotient(field_row: dict) -> dict:
    expected_canonical = str(field_row["canonicalPolynomial"])
    source_rows = field_row.get("sourceRows", [])
    if not source_rows:
        raise ValueError("q191 source rows are absent")
    connection = sqlite3.connect(f"file:{LEDGER.resolve()}?mode=ro", uri=True)
    checked = []
    try:
        for source in source_rows:
            record = connection.execute(
                """
                SELECT v.status,v.label,v.scoreable,p.coefficient_hash,
                       p.coefficients
                FROM verifications AS v
                JOIN polynomials AS p USING(submission_id,polynomial_index)
                WHERE v.submission_id=? AND v.polynomial_index=?
                """,
                (
                    str(source["submissionId"]),
                    int(source["polynomialIndex"]),
                ),
            ).fetchone()
            if record is None:
                raise ValueError("q191 source row disappeared from the ledger")
            status, label, scoreable, coefficient_hash, coefficients_text = record
            if (
                str(status) != "accepted"
                or str(label) != str(source["label"])
                or not bool(scoreable)
                or str(coefficient_hash) != str(source["coefficientSha256"])
            ):
                raise ValueError("q191 source acceptance pin changed")
            coefficients = [ZZ(value) for value in str(coefficients_text).split(",")]
            if (
                len(coefficients) != 25
                or coefficients[-1] != 1
                or any(coefficients[index] for index in range(1, 25, 2))
            ):
                raise ValueError("q191 source polynomial is no longer Q(x^2)")
            raw_quotient = RING(coefficients[::2])
            reduced = RING(pari(raw_quotient).polredabs())
            canonical = ",".join(str(ZZ(value)) for value in reduced)
            if canonical != expected_canonical:
                raise ValueError("q191 census field no longer matches source Q")
            checked.append(
                {
                    "coefficientSha256": str(coefficient_hash),
                    "label": str(label),
                    "polynomialIndex": int(source["polynomialIndex"]),
                    "submissionId": str(source["submissionId"]),
                }
            )
    finally:
        connection.close()
    return {"checkedSources": checked, "quotient": RING([ZZ(v) for v in expected_canonical.split(",")])}


def structural_frobenius_certificate(
    field_row: dict, quotient_t: int, _ring
) -> dict:
    if int(quotient_t) != QUOTIENT_T:
        raise ValueError("q191 launcher received another quotient")
    source_identity = verified_even_quotient(field_row)
    structures = structure_rows()
    possible_sets = []
    block_systems = {}
    for source in source_identity["checkedSources"]:
        label = source["label"]
        if label not in structures:
            raise ValueError(f"structural parent absent for {label}")
        systems = [
            system
            for system in structures[label].get("blockSystems", [])
            if system.get("shape") == "12x2"
        ]
        if not systems:
            raise ValueError(f"{label} has no 12x2 block system")
        possible_sets.append(
            {int(str(system["quotientActionLabel"])[3:]) for system in systems}
        )
        block_systems[label] = systems
    possible = set.intersection(*possible_sets)
    if QUOTIENT_T not in possible:
        raise ValueError("12T191 absent from possible exact block quotients")

    profiles = {value: group_cycle_profiles(value) for value in sorted(possible)}
    remaining = set(possible)
    elimination = []
    quotient = source_identity["quotient"]
    checked_primes = 0
    for prime in prime_range(2, 5001):
        prime = int(prime)
        observed = cycle_type(quotient, prime)
        if observed is None:
            continue
        checked_primes += 1
        reduced = {
            value for value in remaining if observed in profiles[value]
        }
        if len(reduced) < len(remaining):
            elimination.append(
                {
                    "afterLabels": [f"12T{value}" for value in sorted(reduced)],
                    "cycleType": list(observed),
                    "prime": prime,
                }
            )
            remaining = reduced
        if len(remaining) <= 1:
            break
    if remaining != {QUOTIENT_T}:
        raise ValueError(
            "block-quotient Frobenius identity did not isolate 12T191: "
            f"{sorted(remaining)}"
        )
    return {
        "algorithm": (
            "accepted Q(x^2) parent, exhaustive parent 12x2 block systems, "
            "and squarefree Dedekind cycle-type exclusion"
        ),
        "checkedSquarefreePrimes": checked_primes,
        "degree": 12,
        "elimination": elimination,
        "order": QUOTIENT_ORDER,
        "parentBlockSystems": block_systems,
        "possibleLabelsBeforeFrobenius": [
            f"12T{value}" for value in sorted(possible)
        ],
        "sourceIdentity": source_identity["checkedSources"],
        "status": "exact_quotient_group_certified",
        "transitiveLabel": f"12T{QUOTIENT_T}",
        "transitiveNumber": QUOTIENT_T,
    }


DRIVER.load_base = singleton_core_load_base
DRIVER.exact_galois_certificate = structural_frobenius_certificate


if __name__ == "__main__":
    selmer_group._ideal_generator = squareclass_compact_ideal_generator
    raise SystemExit(DRIVER.main())
