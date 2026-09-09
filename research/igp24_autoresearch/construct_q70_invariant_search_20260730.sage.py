#!/usr/bin/env sage
"""Construct totally-real 12T70 fields from a diagonal C3 invariant.

Let f1 and f2 be totally-real S3 cubics and let g be a cyclic cubic,
with linearly disjoint splitting fields.  In

    Gal(L/Q) = S3 x S3 x C3

write the three C3 rotations additively.  The plane i+j+k=0 has order
9 and trivial core.  A generic orbit sum fixed by that plane therefore
has degree 12 and transitive Galois action 12T70.
"""

import argparse
import json
from hashlib import sha256
from pathlib import Path

from sage.all import (
    NumberField,
    Permutation,
    PermutationGroup,
    PolynomialRing,
    QQ,
    RealField,
    ZZ,
    pari,
)
from sage.libs.gap.libgap import libgap


R = PolynomialRing(QQ, "x")
x = R.gen()
ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "character_fresh_field_inventory_q70_diagonal_invariant_20260730.json"
)

PRIOR_HASHES = {
    "6d0c0ed8202fc2aff04f03b6750f07570798f797b1690636711d7bd47d2af018",
}


def canonical_coefficients(poly):
    """Primitive monic PARI-polredabs coefficients, constant first."""
    reduced = R(pari(R(poly)).polredabs())
    den = ZZ(reduced.denominator())
    integral = R(reduced * den)
    coeffs = [ZZ(c) for c in integral.list()]
    content = ZZ(0)
    for c in coeffs:
        content = content.gcd(c)
    if content:
        coeffs = [c // content for c in coeffs]
    if coeffs[-1] < 0:
        coeffs = [-c for c in coeffs]
    if coeffs[-1] != 1:
        raise ValueError("canonical polynomial is not monic")
    return tuple(int(c) for c in coeffs)


def coefficient_hash(coeffs):
    return sha256(",".join(str(c) for c in coeffs).encode("ascii")).hexdigest()


def q70_action_certificate():
    """Return the exact coset action of the diagonal plane."""
    states = [
        (s, eps1, eps2)
        for eps1 in (1, -1)
        for eps2 in (1, -1)
        for s in range(3)
    ]
    indexes = {state: index + 1 for index, state in enumerate(states)}

    def permutation(function):
        return Permutation(
            [indexes[function(*state)] for state in states]
        )

    generators = [
        permutation(lambda s, e1, e2: ((s + e1) % 3, e1, e2)),
        permutation(lambda s, e1, e2: ((s + e2) % 3, e1, e2)),
        permutation(lambda s, e1, e2: ((s + 1) % 3, e1, e2)),
        permutation(lambda s, e1, e2: (s, -e1, e2)),
        permutation(lambda s, e1, e2: (s, e1, -e2)),
    ]
    group = PermutationGroup(generators)
    transitive_number = int(libgap.TransitiveIdentification(group))
    if (
        group.order() != 108
        or not group.is_transitive()
        or transitive_number != 70
    ):
        raise ArithmeticError("diagonal invariant action is not 12T70")
    return {
        "degree": 12,
        "generatorImages": [
            [int(value) for value in generator]
            for generator in generators
        ],
        "generatorSigns": [int(generator.sign()) for generator in generators],
        "groupOrder": 108,
        "method": "exact GAP TransitiveIdentification of diagonal-plane coset action",
        "stateOrder": [list(state) for state in states],
        "transitiveGroupLabel": "12T70",
        "transitiveGroupNumber": transitive_number,
    }


def integer_orbit_polynomial(f1, f2, g, precision=1024):
    """Numerically form the exact integral orbit polynomial.

    All three cubics are monic with integral roots.  The orbit sums are
    algebraic integers and the product over the 12-element Galois orbit
    is in Z[x], so high-precision rounding reconstructs it exactly.
    """
    RF = RealField(precision)
    RR = PolynomialRing(RF, "T")
    T = RR.gen()

    roots1 = sorted(RR(f1).roots(multiplicities=False))
    roots2 = sorted(RR(f2).roots(multiplicities=False))
    roots3 = sorted(RR(g).roots(multiplicities=False))
    if not (len(roots1) == len(roots2) == len(roots3) == 3):
        return None

    values = []
    for eps1 in (1, -1):
        for eps2 in (1, -1):
            for s in range(3):
                value = RF(0)
                for i in range(3):
                    for j in range(3):
                        k = (s - i - j) % 3
                        value += roots1[(eps1 * i) % 3] * roots2[(eps2 * j) % 3] * roots3[k]
                values.append(value)

    numerical = RR(1)
    for value in values:
        numerical *= T - value
    coeffs = [ZZ(c.round()) for c in numerical.list()]
    max_error = max(abs(numerical[i] - RF(coeffs[i])) for i in range(13))
    if max_error > RF(2) ** (-(precision // 3)):
        raise ArithmeticError("insufficient reconstruction precision: %s" % max_error)
    return R(coeffs), values, max_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to overwrite q70 fresh-field inventory")

    # Small totally-real S3 cubics with pairwise-distinct quadratic
    # resolvents, and small cyclic cubics.  The first examples normally
    # suffice; the lists make the script a reusable fresh-field source.
    s3_cubics = [
        x**3 - 4*x + 1,             # disc 229
        x**3 - 5*x + 1,             # disc 473
        x**3 - x**2 - 3*x + 1,      # disc 148 = 4*37
        x**3 - 2*x**2 - 3*x + 1,    # disc 257
        x**3 - 6*x + 1,             # disc 837 = 3^3*31
        x**3 - x**2 - 4*x + 1,      # disc 321 = 3*107
    ]
    cyclic_cubics = [
        x**3 + x**2 - 2*x - 1,      # conductor 7, disc 7^2
        x**3 - 3*x - 1,             # conductor 9, disc 3^4
        x**3 + x**2 - 4*x + 1,      # conductor 13, disc 13^2
    ]

    action_certificate = q70_action_certificate()
    seen = set(PRIOR_HASHES)
    rows = []
    for a, f1 in enumerate(s3_cubics):
        if not f1.is_irreducible() or len(f1.real_roots()) != 3:
            continue
        for f2 in s3_cubics[a + 1:]:
            if not f2.is_irreducible() or len(f2.real_roots()) != 3:
                continue
            # Distinct square classes of discriminants guarantee that
            # the two S3 splitting fields do not share their quadratic
            # subfield.
            d1 = ZZ(f1.discriminant()).squarefree_part()
            d2 = ZZ(f2.discriminant()).squarefree_part()
            if d1 in (1, d2):
                continue
            for g in cyclic_cubics:
                if not g.is_irreducible() or g.discriminant().is_square() is False:
                    continue
                candidate, roots, error = integer_orbit_polynomial(f1, f2, g)
                if candidate is None or candidate.degree() != 12:
                    continue
                candidate_check, _roots_check, error_check = (
                    integer_orbit_polynomial(f1, f2, g, precision=1536)
                )
                if candidate_check != candidate:
                    raise ArithmeticError(
                        "orbit reconstruction changed at higher precision"
                    )
                if not candidate.is_irreducible():
                    continue
                K = NumberField(candidate, "a")
                if K.signature() != (12, 0):
                    continue
                coeffs = canonical_coefficients(candidate)
                digest = coefficient_hash(coeffs)
                if digest in seen:
                    continue
                seen.add(digest)
                rows.append(
                    {
                        "alreadyAudited": False,
                        "canonicalPolynomial": ",".join(
                            str(value) for value in coeffs
                        ),
                        "construction": {
                            "cyclicCubic": str(g),
                            "cyclicCubicDiscriminant": int(
                                g.discriminant()
                            ),
                            "firstS3Cubic": str(f1),
                            "firstS3CubicDiscriminant": int(
                                f1.discriminant()
                            ),
                            "firstS3QuadraticResolventSquareclass": int(d1),
                            "invariant": (
                                "sum_{i+j+k=s} "
                                "a_{eps1*i} b_{eps2*j} c_k"
                            ),
                            "secondS3Cubic": str(f2),
                            "secondS3CubicDiscriminant": int(
                                f2.discriminant()
                            ),
                            "secondS3QuadraticResolventSquareclass": int(d2),
                        },
                        "fieldCanonicalSha256": digest,
                        "fieldDiscriminant": int(
                            NumberField(R(coeffs), "b").discriminant()
                        ),
                        "family": "12T70",
                        "provenance": (
                            "exact diagonal C3-plane invariant in the "
                            "linearly-disjoint totally-real compositum of two "
                            "S3 cubic splitting fields and one cyclic cubic; "
                            "the exact 12-point coset action is GAP-identified "
                            "as 12T70; integral orbit coefficients were stable "
                            "at 1024 and 1536 bits and then PARI-polredabs "
                            "canonicalized"
                        ),
                        "quotientT12": 70,
                        "rawPolynomial": ",".join(
                            str(int(value)) for value in candidate.list()
                        ),
                        "reconstructionErrors": {
                            "bits1024": str(error),
                            "bits1536": str(error_check),
                        },
                        "sourceRows": [
                            {
                                "construction": "diagonal_C3_plane_invariant",
                                "label": "synthetic-12T70",
                            }
                        ],
                    }
                )

    rows.sort(key=lambda row: row["fieldCanonicalSha256"])
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "freshFieldCount": len(rows),
            "networkCalls": 0,
            "priorFieldHashesExcluded": sorted(PRIOR_HASHES),
            "reconstructionPrecisionsBits": [1024, 1536],
            "submissionCalls": 0,
        },
        "constructionProof": {
            "actionCertificate": action_certificate,
            "linearlyDisjointCriterion": (
                "the irreducible totally-real S3 cubics have distinct "
                "nontrivial quadratic-resolvent squareclasses; their normal "
                "closures therefore intersect trivially, while their product "
                "has no cyclic cubic quotient and is disjoint from the "
                "independent cyclic cubic"
            ),
            "totalRealityCriterion": (
                "all roots of all three source cubics are real, hence every "
                "diagonal invariant and every conjugate is real"
            ),
        },
        "freshCanonicalTotallyRealFields": rows,
        "quotientT12": 70,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "freshFields": len(rows),
                "output": str(args.output.resolve()),
                "sha256": sha256(rendered.encode()).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
