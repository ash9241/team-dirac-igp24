#!/usr/bin/env sage
"""Construct a degree-24 shared-quadratic compositum.

The pilot uses K0 = Q(i), a generic relative S4 quartic and a generic
relative S3 cubic.  Their norm fields have degrees 8 and 6, contain K0, and
their compatible compositum has degree 24.  Pairwise disjoint relative
discriminant support gives the full fiber product

    (S4 wr C2) x_{C2} (S3 wr C2)

in its natural compatible-pairs action.
"""

import json
import random
import sys

from sage.all import (
    QQ,
    ZZ,
    PolynomialRing,
    QuadraticField,
    SymmetricGroup,
    gcd,
)
from sage.libs.gap.libgap import libgap


def compatible_fiber_product_group():
    """Return the compatible-pairs action and its 24T identifier."""
    points = [
        (i, j)
        for i in range(8)
        for j in range(6)
        if (i // 4) == (j // 3)
    ]
    point_index = {point: k + 1 for k, point in enumerate(points)}

    def perm8(cycles):
        images = list(range(8))
        for cycle in cycles:
            for u, v in zip(cycle, cycle[1:] + cycle[:1]):
                images[u] = v
        return images

    def perm6(cycles):
        images = list(range(6))
        for cycle in cycles:
            for u, v in zip(cycle, cycle[1:] + cycle[:1]):
                images[u] = v
        return images

    id8 = list(range(8))
    id6 = list(range(6))
    a_kernel = [
        perm8([(0, 1)]),
        perm8([(0, 1, 2, 3)]),
        perm8([(4, 5)]),
        perm8([(4, 5, 6, 7)]),
    ]
    b_kernel = [
        perm6([(0, 1)]),
        perm6([(0, 1, 2)]),
        perm6([(3, 4)]),
        perm6([(3, 4, 5)]),
    ]
    swap8 = perm8([(0, 4), (1, 5), (2, 6), (3, 7)])
    swap6 = perm6([(0, 3), (1, 4), (2, 5)])

    pairs = [(a, id6) for a in a_kernel]
    pairs += [(id8, b) for b in b_kernel]
    pairs += [(swap8, swap6)]

    S24 = SymmetricGroup(24)
    generators = []
    for a, b in pairs:
        images = [
            point_index[(a[i], b[j])]
            for i, j in points
        ]
        generators.append(S24(images))
    group = S24.subgroup(generators)
    if not group.is_transitive():
        raise RuntimeError("compatible-pairs group is not transitive")
    if group.order() != 41472:
        raise RuntimeError("unexpected fiber-product order %s" % group.order())
    t_number = int(libgap.TransitiveIdentification(group))
    return group, t_number


def conjugate_polynomial(poly, sigma, ring):
    x = ring.gen()
    return sum(sigma(poly[i]) * x**i for i in range(poly.degree() + 1))


def rational_norm_polynomial(poly, sigma):
    ring_k = poly.parent()
    product = poly * conjugate_polynomial(poly, sigma, ring_k)
    ring_q = PolynomialRing(QQ, "x")
    coeffs = []
    for coefficient in product:
        if coefficient != sigma(coefficient):
            raise RuntimeError("norm coefficient is not rational")
        coeffs.append(QQ(coefficient))
    result = ring_q(coeffs)
    if not result.is_monic():
        result = result.monic()
    return result


def quartic_resolvent(poly):
    ring = poly.parent()
    y = ring.gen()
    a = poly[3]
    b = poly[2]
    c = poly[1]
    d = poly[0]
    return y**3 - b*y**2 + (a*c - 4*d)*y + (
        4*b*d - a*a*d - c*c
    )


def support(ideal):
    return {prime for prime, _ in ideal.factor()}


def supports_are_disjoint(*ideals):
    seen = set()
    for ideal in ideals:
        current = support(ideal)
        if seen.intersection(current):
            return False
        seen.update(current)
    return True


def search_relative_sources(seed=20260727, attempts=20000):
    random.seed(seed)
    base = QuadraticField(-1, "w")
    w = base.gen()
    sigma = base.hom([-w], base)
    ring = PolynomialRing(base, "z")
    z = ring.gen()

    quartic = None
    quartic_ideals = None
    for _ in range(attempts):
        coefficients = [
            base(random.randint(-5, 5) + random.randint(-5, 5)*w)
            for _ in range(4)
        ]
        candidate = z**4 + sum(
            coefficients[i] * z**i for i in range(4)
        )
        if not candidate.is_irreducible():
            continue
        disc = candidate.discriminant()
        if disc.is_square():
            continue
        if not quartic_resolvent(candidate).is_irreducible():
            continue
        conjugate_disc = sigma(disc)
        ideals = (base.ideal(disc), base.ideal(conjugate_disc))
        if not supports_are_disjoint(*ideals):
            continue
        norm_poly = rational_norm_polynomial(candidate, sigma)
        if norm_poly.degree() != 8 or not norm_poly.is_irreducible():
            continue
        quartic = candidate
        quartic_ideals = ideals
        break
    if quartic is None:
        raise RuntimeError("no certified relative S4 quartic found")

    cubic = None
    for _ in range(attempts):
        coefficients = [
            base(random.randint(-7, 7) + random.randint(-7, 7)*w)
            for _ in range(3)
        ]
        candidate = z**3 + sum(
            coefficients[i] * z**i for i in range(3)
        )
        if not candidate.is_irreducible():
            continue
        disc = candidate.discriminant()
        if disc.is_square():
            continue
        conjugate_disc = sigma(disc)
        ideals = (
            base.ideal(disc),
            base.ideal(conjugate_disc),
        )
        if not supports_are_disjoint(*(quartic_ideals + ideals)):
            continue
        norm_poly = rational_norm_polynomial(candidate, sigma)
        if norm_poly.degree() != 6 or not norm_poly.is_irreducible():
            continue
        cubic = candidate
        break
    if cubic is None:
        raise RuntimeError("no certified relative S3 cubic found")

    return base, sigma, quartic, cubic


def integral_monic(poly):
    poly = poly.monic()
    if any(coefficient.denominator() != 1 for coefficient in poly):
        raise RuntimeError("candidate factor is not integral")
    ring_z = PolynomialRing(ZZ, "x")
    return ring_z([ZZ(coefficient) for coefficient in poly])


def compatible_compositum_factors(poly8, poly6):
    bivariate = PolynomialRing(QQ, names=("X", "Y"))
    X, Y = bivariate.gens()
    q_y = sum(poly8[i] * Y**i for i in range(poly8.degree() + 1))
    p_shift = sum(
        poly6[i] * (X - Y)**i for i in range(poly6.degree() + 1)
    )
    resultant = q_y.resultant(p_shift, Y)
    univariate = PolynomialRing(QQ, "X")
    resultant = univariate(resultant)
    factors = []
    for factor, exponent in resultant.factor():
        if exponent != 1:
            raise RuntimeError("non-squarefree compositum resultant")
        if factor.degree() == 24:
            factors.append(integral_monic(factor))
    if len(factors) != 2:
        raise RuntimeError(
            "expected two degree-24 compatible factors, got degrees %r"
            % [factor.degree() for factor, _ in resultant.factor()]
        )
    return factors


def main():
    group, t_number = compatible_fiber_product_group()
    base, sigma, quartic, cubic = search_relative_sources()
    poly8 = rational_norm_polynomial(quartic, sigma)
    poly6 = rational_norm_polynomial(cubic, sigma)
    factors = compatible_compositum_factors(poly8, poly6)

    payload = {
        "construction": "shared_quadratic_fiber_product",
        "quadratic_base": "Q(sqrt(-1))",
        "predicted_group": "24T%d" % t_number,
        "predicted_group_order": int(group.order()),
        "relative_quartic": str(quartic),
        "relative_cubic": str(cubic),
        "degree8_norm_coefficients": [int(c) for c in poly8],
        "degree6_norm_coefficients": [int(c) for c in poly6],
        "degree24_candidates": [
            {
                "coefficients": [int(c) for c in factor],
                "irreducible": bool(factor.is_irreducible()),
                "real_roots": int(factor.number_of_real_roots()),
            }
            for factor in factors
        ],
        "certificate": {
            "quartic_irreducible": bool(quartic.is_irreducible()),
            "quartic_resolvent_irreducible": bool(
                quartic_resolvent(quartic).is_irreducible()
            ),
            "quartic_discriminant_nonsquare": bool(
                not quartic.discriminant().is_square()
            ),
            "cubic_irreducible": bool(cubic.is_irreducible()),
            "cubic_discriminant_nonsquare": bool(
                not cubic.discriminant().is_square()
            ),
            "quadratic_base_class_number": int(base.class_number()),
            "relative_discriminant_supports_pairwise_disjoint": True,
        },
    }
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
