#!/usr/bin/env python3
"""Fail-closed classifier for rank-3 affine 12T30 lifts of degree 24.

The input polynomial has the form

    F(x) = A^12 q((x^2 - B) / A) = P(x^2),

where q is monic of degree 12.  An upstream exact action certificate must
already prove that q has group 12T30 and that F has order 384, kernel order
8, and exhaustive compatible-label set

    {24T839, 24T943, 24T1132}.

This script does not compute a Galois group.  It uses the exact discriminant
character separating those three actions:

* 24T839 and 24T1132 lie in A_24, so disc(F) is a square;
* 24T943 has the same nontrivial sign character as its natural 12T30
  quotient, so disc(F) and disc(q) have the same nonsquare class.

For degree 12,

    disc(P(x^2)) = 4^12 P(0) disc(P)^2,

and the affine change from q to P changes disc(q) by A^132, a square.
Consequently P(0) alone supplies the degree-24 discriminant squareclass.

The classifier emits ``certified_24T943`` only inside the exact exhaustive
three-label scope above.  Any missing, inconsistent, or broader evidence is
reported as ``fail_closed``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


COMPATIBLE_LABELS = frozenset({"24T839", "24T943", "24T1132"})

LABEL_CERTIFICATES = {
    "24T839": {
        "smallGroupId": [384, 20099],
        "abelianInvariants": [2, 4],
        "quadraticSubfieldCount": 3,
        "sign24": "trivial",
        "natural12T30Systems": 2,
        "naturalDiscriminantRelation": "P(0) is a rational square",
    },
    "24T943": {
        "smallGroupId": [384, 574],
        "abelianInvariants": [8],
        "quadraticSubfieldCount": 1,
        "sign24": "equals_natural_12T30_sign",
        "natural12T30Systems": 1,
        "naturalDiscriminantRelation": (
            "P(0) and disc(q) have the same nonsquare class"
        ),
    },
    "24T1132": {
        "smallGroupId": [384, 20099],
        "abelianInvariants": [2, 4],
        "quadraticSubfieldCount": 3,
        "sign24": "trivial",
        "natural12T30Systems": 1,
        "naturalDiscriminantRelation": "P(0) is a rational square",
    },
}


class FailClosed(ValueError):
    """Raised when the exact certification scope is not established."""


def trim(coefficients: list[int]) -> list[int]:
    result = list(coefficients)
    while len(result) > 1 and result[-1] == 0:
        result.pop()
    return result


def parse_coefficients(value: Any, field: str) -> list[int]:
    if isinstance(value, str):
        raw = value.split("#", 1)[0].strip()
        pieces = [piece.strip() for piece in raw.split(",")]
        if not raw or any(piece == "" for piece in pieces):
            raise FailClosed(f"{field} is not a canonical coefficient line")
        try:
            result = [int(piece) for piece in pieces]
        except ValueError as error:
            raise FailClosed(f"{field} has a nonintegral coefficient") from error
    elif isinstance(value, list):
        if not value or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
            raise FailClosed(f"{field} must be a nonempty integer list")
        result = list(value)
    else:
        raise FailClosed(f"{field} must be a coefficient line or integer list")
    if trim(result) != result:
        raise FailClosed(f"{field} has trailing zero coefficients")
    return result


def determinant_bareiss(matrix: list[list[int]]) -> int:
    """Return an exact determinant using fraction-free elimination."""
    size = len(matrix)
    if size == 0:
        return 1
    if any(len(row) != size for row in matrix):
        raise ValueError("determinant input is not square")
    work = [list(row) for row in matrix]
    sign = 1
    previous = 1
    for pivot_index in range(size - 1):
        if work[pivot_index][pivot_index] == 0:
            swap = next(
                (
                    row
                    for row in range(pivot_index + 1, size)
                    if work[row][pivot_index] != 0
                ),
                None,
            )
            if swap is None:
                return 0
            work[pivot_index], work[swap] = work[swap], work[pivot_index]
            sign = -sign
        pivot = work[pivot_index][pivot_index]
        for row in range(pivot_index + 1, size):
            for column in range(pivot_index + 1, size):
                numerator = (
                    work[row][column] * pivot
                    - work[row][pivot_index] * work[pivot_index][column]
                )
                quotient, remainder = divmod(numerator, previous)
                if remainder:
                    raise ArithmeticError("Bareiss division was not exact")
                work[row][column] = quotient
        previous = pivot
        for row in range(pivot_index + 1, size):
            work[row][pivot_index] = 0
        for column in range(pivot_index + 1, size):
            work[pivot_index][column] = 0
    return sign * work[-1][-1]


def resultant(first: list[int], second: list[int]) -> int:
    """Return the exact resultant of ascending integral polynomials."""
    first = trim(first)
    second = trim(second)
    first_degree = len(first) - 1
    second_degree = len(second) - 1
    if first_degree < 0 or second_degree < 0:
        raise ValueError("resultant input is empty")
    if first_degree == 0:
        return first[0] ** second_degree
    if second_degree == 0:
        return second[0] ** first_degree
    first_descending = list(reversed(first))
    second_descending = list(reversed(second))
    size = first_degree + second_degree
    matrix: list[list[int]] = []
    for shift in range(second_degree):
        matrix.append(
            [0] * shift
            + first_descending
            + [0] * (size - shift - len(first_descending))
        )
    for shift in range(first_degree):
        matrix.append(
            [0] * shift
            + second_descending
            + [0] * (size - shift - len(second_descending))
        )
    return determinant_bareiss(matrix)


def discriminant(coefficients: list[int]) -> int:
    coefficients = trim(coefficients)
    degree = len(coefficients) - 1
    if degree <= 0:
        raise FailClosed("quotient polynomial must have positive degree")
    derivative = [
        index * coefficients[index] for index in range(1, len(coefficients))
    ]
    value = resultant(coefficients, derivative)
    if degree * (degree - 1) // 2 % 2:
        value = -value
    leading = coefficients[-1]
    quotient, remainder = divmod(value, leading)
    if remainder:
        raise ArithmeticError("discriminant division was not exact")
    return quotient


def affine_quotient(
    quotient_coefficients: list[int], scale: int, shift: int
) -> list[int]:
    """Build P(y)=A^12*q((y-B)/A) with ascending coefficients."""
    if len(quotient_coefficients) != 13 or quotient_coefficients[-1] != 1:
        raise FailClosed("quotient polynomial must be monic of exact degree 12")
    if isinstance(scale, bool) or not isinstance(scale, int) or scale == 0:
        raise FailClosed("A must be a nonzero integer")
    if isinstance(shift, bool) or not isinstance(shift, int):
        raise FailClosed("B must be an integer")
    degree = 12
    result = [0] * (degree + 1)
    for exponent, coefficient in enumerate(quotient_coefficients):
        multiplier = coefficient * scale ** (degree - exponent)
        for power in range(exponent + 1):
            result[power] += (
                multiplier
                * math.comb(exponent, power)
                * (-shift) ** (exponent - power)
            )
    if result[-1] != 1:
        raise ArithmeticError("affine quotient unexpectedly lost monicity")
    return result


def quadratic_lift(quotient_coefficients: list[int]) -> list[int]:
    result = [0] * 25
    for index, coefficient in enumerate(quotient_coefficients):
        result[2 * index] = coefficient
    return result


def square_witness(value: int) -> int | None:
    if value < 0:
        return None
    root = math.isqrt(value)
    return root if root * root == value else None


def same_squareclass_witness(first: int, second: int) -> int | None:
    if first == 0 or second == 0 or (first < 0) != (second < 0):
        return None
    return square_witness(abs(first * second))


def coefficient_sha256(coefficients: list[int]) -> str:
    line = ",".join(str(value) for value in coefficients)
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def exact_scope(packet: dict[str, Any]) -> dict[str, Any]:
    evidence = packet.get("upstreamCertificate")
    if not isinstance(evidence, dict):
        raise FailClosed("upstreamCertificate is missing")
    if evidence.get("exact") is not True:
        raise FailClosed("upstreamCertificate.exact must be true")
    if str(evidence.get("quotientLabel")) != "12T30":
        raise FailClosed("the quotient label is not exactly 12T30")
    if int(evidence.get("groupOrder", -1)) != 384:
        raise FailClosed("the candidate group order is not exactly 384")
    if int(evidence.get("kernelOrder", -1)) != 8:
        raise FailClosed("the natural 12-block kernel order is not exactly 8")
    labels = evidence.get("compatibleLabels")
    if not isinstance(labels, list) or {str(label) for label in labels} != COMPATIBLE_LABELS:
        raise FailClosed("the exhaustive compatible-label set is not exact")
    return evidence


def classify_packet(packet: dict[str, Any]) -> dict[str, Any]:
    try:
        evidence = exact_scope(packet)
        q = parse_coefficients(packet.get("quotientCoefficients"), "quotientCoefficients")
        candidate = parse_coefficients(
            packet.get("candidateCoefficients"), "candidateCoefficients"
        )
        scale = packet.get("A")
        shift = packet.get("B")
        p = affine_quotient(q, scale, shift)
        expected_candidate = quadratic_lift(p)
        if candidate != expected_candidate:
            raise FailClosed(
                "candidateCoefficients do not equal A^12*q((x^2-B)/A)"
            )
        quotient_discriminant = discriminant(q)
        if quotient_discriminant == 0:
            raise FailClosed("the quotient polynomial is inseparable")
        if square_witness(quotient_discriminant) is not None:
            raise FailClosed(
                "disc(q) is square, contradicting the certified 12T30 sign"
            )
        norm = p[0]
        if norm == 0:
            raise FailClosed("P(0)=0, so the degree-24 polynomial is inseparable")
        norm_square = square_witness(norm)
        same_class = same_squareclass_witness(norm, quotient_discriminant)
        common = {
            "method": "exact_affine_discriminant_character",
            "candidateCoefficientSha256": coefficient_sha256(candidate),
            "quotientCoefficientSha256": coefficient_sha256(q),
            "A": scale,
            "B": shift,
            "P0": str(norm),
            "quotientDiscriminant": str(quotient_discriminant),
            "upstreamCertificate": evidence,
            "labelCertificates": LABEL_CERTIFICATES,
            "identity": "disc(P(x^2))=4^12*P(0)*disc(P)^2",
            "affineDiscriminantRelation": "disc(P)=A^132*disc(q)",
        }
        if norm_square is not None:
            return {
                "status": "reject_24T943",
                "certifiedLabel": None,
                "compatibleLabels": ["24T839", "24T1132"],
                "reason": (
                    "P(0) is a rational square, hence disc(F) is square; "
                    "24T943 has the nontrivial 12T30 sign character"
                ),
                "squareWitnessForP0": str(norm_square),
                **common,
            }
        if same_class is not None:
            return {
                "status": "certified_24T943",
                "certifiedLabel": "24T943",
                "compatibleLabels": ["24T943"],
                "reason": (
                    "P(0) and disc(q) have the same nonsquare class; among the "
                    "exact exhaustive order-384 rank-3 siblings only 24T943 "
                    "has sign24 equal to the natural 12T30 sign"
                ),
                "squareWitnessForP0TimesDiscQ": str(same_class),
                **common,
            }
        return {
            "status": "reject_all_three",
            "certifiedLabel": None,
            "compatibleLabels": [],
            "reason": (
                "P(0) is neither square nor in the disc(q) squareclass; this "
                "contradicts the exact exhaustive three-label upstream scope"
            ),
            **common,
        }
    except (FailClosed, TypeError, ValueError) as error:
        return {
            "status": "fail_closed",
            "certifiedLabel": None,
            "compatibleLabels": [],
            "reason": str(error),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="input JSON classifier packet")
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    result = classify_packet(packet)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] != "fail_closed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
