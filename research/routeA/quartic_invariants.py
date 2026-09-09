#!/usr/bin/env python3
"""Exact quartic invariants and modular fingerprints for GQ pilots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import sympy


@dataclass(frozen=True)
class QuarticRegime:
    name: str
    resolvent_factor_degrees: tuple[int, ...]
    discriminant_square: bool


def cubic_resolvent_coefficients(
    a: object,
    b: object,
    c: object,
    d: object,
) -> tuple[object, object, object, int]:
    """Return ascending coefficients of a standard monic quartic resolvent.

    For ``x^4+a*x^3+b*x^2+c*x+d`` the convention is

    ``z^3-b*z^2+(a*c-4*d)*z+(4*b*d-a^2*d-c^2)``.
    """

    return 4 * b * d - a * a * d - c * c, a * c - 4 * d, -b, 1


def depressed_quartic_discriminant(p: object, q: object, r: object) -> object:
    """Discriminant of ``x^4+p*x^2+q*x+r``."""

    return (
        256 * r**3
        - 128 * p**2 * r**2
        + 144 * p * q**2 * r
        - 27 * q**4
        + 16 * p**4 * r
        - 4 * p**3 * q**2
    )


def classify_relative_quartic(
    factor_degrees: Iterable[int], discriminant_square: bool
) -> QuarticRegime:
    """Classify an irreducible quartic from its cubic resolvent."""

    degrees = tuple(sorted(int(value) for value in factor_degrees))
    if degrees == (3,):
        name = "A4" if discriminant_square else "S4"
    elif degrees == (1, 2):
        name = "C4_or_V4" if discriminant_square else "D4"
    elif degrees == (1, 1, 1):
        name = "V4"
    else:
        name = "unclassified"
    return QuarticRegime(name, degrees, bool(discriminant_square))


def old_form_regime(form: str) -> str:
    value = str(form)
    if value == "pure4" or value.startswith("d4-"):
        return "D4_linked"
    if value == "c4":
        return "C4_linked"
    if value == "v4-entangled":
        return "V4_common"
    return "unknown"


def predicted_old_label(form: str) -> int | None:
    return {
        "D4_linked": 19036,
        "C4_linked": 17757,
        "V4_common": 7181,
    }.get(old_form_regime(form))


def frobenius_partition(coefficients: Sequence[int], prime: int) -> tuple[int, ...] | None:
    """Return the squarefree factor-degree partition modulo ``prime``."""

    x = sympy.Symbol("x")
    polynomial = sympy.Poly(
        sum(int(value) * x**index for index, value in enumerate(coefficients)),
        x,
        modulus=int(prime),
    )
    if polynomial.degree() != len(coefficients) - 1:
        return None
    if sympy.gcd(polynomial, polynomial.diff()).degree() > 0:
        return None
    _, factors = sympy.factor_list(polynomial, modulus=int(prime))
    degrees: list[int] = []
    for factor, multiplicity in factors:
        degrees.extend([int(factor.degree())] * int(multiplicity))
    return tuple(sorted(degrees, reverse=True))


def frobenius_fingerprint(
    coefficients: Sequence[int], primes: Iterable[int]
) -> dict[int, tuple[int, ...]]:
    fingerprint = {}
    for prime in primes:
        partition = frobenius_partition(coefficients, int(prime))
        if partition is not None:
            fingerprint[int(prime)] = partition
    return fingerprint

