#!/usr/bin/env sage -python
"""Construct exact degree-24 fields from the unordered 3-subset action."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
PROFILES = ROOT / "data" / "agent_gold_a_triple_current_profiles.jsonl"


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_source(submission_id: str, polynomial_index: int) -> dict:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.t,v.r,v.scoreable
            FROM polynomials p JOIN verifications v
              USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (submission_id, polynomial_index),
        ).fetchone()
    finally:
        connection.close()
    if row is None or int(row["scoreable"] or 0) != 1:
        raise ValueError("source is not a scoreable verified ledger polynomial")
    return dict(row)


def load_profile(source_label: str) -> dict:
    for line in PROFILES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if str(row["sourceLabel"]) == source_label:
            if row.get("status") != "certified":
                raise ValueError("triple profile is not certified")
            return row
    raise ValueError(f"no certified triple profile for {source_label}")


def root_power_sums(coefficients: list[int], maximum: int) -> list[int]:
    degree = len(coefficients) - 1
    if degree != 24 or coefficients[-1] != 1:
        raise ValueError("source polynomial must be monic degree 24")
    descending = [0] + [coefficients[degree - i] for i in range(1, degree + 1)]
    powers = [0] * (maximum + 1)
    powers[0] = degree
    for k in range(1, maximum + 1):
        if k <= degree:
            value = sum(descending[i] * powers[k - i] for i in range(1, k))
            value += k * descending[k]
        else:
            value = sum(
                descending[i] * powers[k - i] for i in range(1, degree + 1)
            )
        powers[k] = -value
    return powers


def transformed_power_sums(root_powers: list[int], maximum: int, c: int) -> list[int]:
    if c == 0:
        return root_powers[: maximum + 1]
    transformed = [0] * (maximum + 1)
    transformed[0] = root_powers[0]
    for k in range(1, maximum + 1):
        transformed[k] = sum(
            math.comb(k, a) * (c**a) * root_powers[k + a]
            for a in range(k + 1)
        )
    return transformed


def triple_sum_resolvent(ring, powers: list[int], degree: int):
    """Newton resolvent for sums over distinct unordered triples."""
    ordered_pair_powers = [0] * (degree + 1)
    for m in range(degree + 1):
        ordered_pair_powers[m] = sum(
            math.comb(m, a) * powers[a] * powers[m - a]
            for a in range(m + 1)
        )

    triple_powers = [0] * (degree + 1)
    triple_powers[0] = degree
    for m in range(1, degree + 1):
        all_ordered = sum(
            math.comb(m, a) * powers[a] * ordered_pair_powers[m - a]
            for a in range(m + 1)
        )
        one_equal_pair = sum(
            math.comb(m, c)
            * (2 ** (m - c))
            * powers[m - c]
            * powers[c]
            for c in range(m + 1)
        )
        numerator = all_ordered - 3 * one_equal_pair + 2 * (3**m) * powers[m]
        if numerator % 6:
            raise ArithmeticError(f"nonintegral triple power sum at m={m}")
        triple_powers[m] = numerator // 6

    elementary = [0] * (degree + 1)
    elementary[0] = 1
    for k in range(1, degree + 1):
        numerator = sum(
            ((-1) ** (m - 1)) * elementary[k - m] * triple_powers[m]
            for m in range(1, k + 1)
        )
        if numerator % k:
            raise ArithmeticError(f"nonintegral elementary sum at k={k}")
        elementary[k] = numerator // k

    coefficients = [0] * (degree + 1)
    for k in range(degree + 1):
        coefficients[degree - k] = ((-1) ** k) * elementary[k]
    return ring(coefficients)


def factor_certificate(resolvent, expected_degrees: list[int], expected_24: int):
    if not resolvent.is_squarefree():
        return None, {"reason": "resolvent_not_squarefree"}
    factorization = list(resolvent.factor())
    actual = sorted(
        int(factor.degree())
        for factor, exponent in factorization
        for _ in range(int(exponent))
    )
    exponents = [int(exponent) for _factor, exponent in factorization]
    certificate = {
        "actualDegrees": actual,
        "expectedDegrees": sorted(expected_degrees),
        "exponents": exponents,
    }
    if any(value != 1 for value in exponents) or actual != sorted(expected_degrees):
        certificate["reason"] = "factorization_does_not_match_exact_triple_orbits"
        return None, certificate
    factors = [factor for factor, _exponent in factorization if factor.degree() == 24]
    if len(factors) != expected_24:
        certificate["reason"] = "degree_24_factor_count_mismatch"
        return None, certificate
    return factors, certificate


def reduce_polynomial(polynomial, mode: str):
    if mode == "none":
        return polynomial
    if mode == "best":
        return polynomial.parent()(pari(polynomial).polredbest())
    if mode == "abs":
        return polynomial.parent()(pari(polynomial).polredabs())
    raise ValueError(mode)


def coefficient_line(polynomial) -> str:
    values = [int(value) for value in polynomial.list()]
    values += [0] * (25 - len(values))
    if len(values) != 25 or values[-1] != 1 or values[0] == 0:
        raise ValueError("candidate is not a valid monic degree-24 line")
    return ",".join(str(value) for value in values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_id")
    parser.add_argument("polynomial_index", type=int)
    parser.add_argument("--transforms", default="0,1")
    parser.add_argument("--reduce", choices=("none", "best", "abs"), default="best")
    parser.add_argument("--nfdisc", action="store_true")
    args = parser.parse_args()

    source = load_source(args.submission_id, args.polynomial_index)
    source_label = str(source["label"])
    profile = load_profile(source_label)
    labels = {str(row["targetLabel"]) for row in profile["targets"]}
    if labels != {source_label}:
        raise ValueError("pilot requires every degree-24 triple orbit to have one common label")
    coefficients = [int(value) for value in str(source["coefficients"]).split(",")]
    ring = PolynomialRing(ZZ, "x")
    source_polynomial = ring(coefficients)
    if not source_polynomial.is_irreducible():
        raise ValueError("source failed local irreducibility")

    degree = math.comb(24, 3)
    root_powers = root_power_sums(coefficients, 2 * degree)
    selected = None
    selected_transform = None
    orbit_certificate = None
    attempts = []
    started = time.monotonic()
    for transform in [int(value) for value in args.transforms.split(",") if value.strip()]:
        attempt_started = time.monotonic()
        transformed = transformed_power_sums(root_powers, degree, transform)
        resolvent = triple_sum_resolvent(ring, transformed, degree)
        resolvent_hash = hashlib.sha256(
            ",".join(str(int(value)) for value in resolvent.list()).encode("ascii")
        ).hexdigest()
        build_seconds = time.monotonic() - attempt_started
        log(f"c={transform}: built degree {degree} in {build_seconds:.3f}s")
        factor_started = time.monotonic()
        selected, orbit_certificate = factor_certificate(
            resolvent,
            [int(value) for value in profile["orbitSizes"]],
            int(profile["length24OrbitCount"]),
        )
        attempts.append(
            {
                "buildSeconds": round(build_seconds, 3),
                "certificate": orbit_certificate,
                "factorSeconds": round(time.monotonic() - factor_started, 3),
                "resolventSha256": resolvent_hash,
                "transform": transform,
            }
        )
        if selected is not None:
            selected_transform = transform
            break
    if selected is None:
        print(json.dumps({"actionKind": "unordered_3_subset", "attempts": attempts, "status": "no_separating_transform"}, sort_keys=True))
        return 2

    candidates = []
    for factor_index, factor in enumerate(selected):
        candidate = reduce_polynomial(factor, args.reduce)
        if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("reduced candidate failed exact polynomial checks")
        line = coefficient_line(candidate)
        row = {
            "coefficientBytes": len(line.encode("ascii")),
            "coefficientLine": line,
            "coefficientSha256": hashlib.sha256(line.encode("ascii")).hexdigest(),
            "factorIndex": factor_index,
            "polynomialDiscriminantAbs": str(abs(int(candidate.discriminant()))),
            "targetLabel": source_label,
            "targetR": int(candidate.number_of_real_roots()),
        }
        if args.nfdisc:
            nf_started = time.monotonic()
            row["fieldDiscriminantAbs"] = str(abs(int(pari(candidate).nfdisc())))
            row["nfdiscSeconds"] = round(time.monotonic() - nf_started, 3)
        candidates.append(row)
    output = {
        "actionKind": "unordered_3_subset",
        "attempts": attempts,
        "candidates": candidates,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "exactProfileSha256": profile["exactProfileSha256"],
        "orbitCertificate": orbit_certificate,
        "orbitTargets": profile["targets"],
        "reduction": args.reduce,
        "sourceCoefficientSha256": source["coefficient_hash"],
        "sourceLabel": source_label,
        "sourcePolynomialIndex": args.polynomial_index,
        "sourceR": int(source["r"]),
        "sourceSubmissionId": args.submission_id,
        "status": "certified_multi",
        "transform": {"c": selected_transform, "kind": "x+c*x^2"},
    }
    print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
