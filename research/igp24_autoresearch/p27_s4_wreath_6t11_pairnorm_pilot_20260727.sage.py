#!/usr/bin/env sage
"""Sparse S4-over-6T11 discriminant-pair-norm pilot.

For integers k and m, put

    L = 4 k (9 m^2 + 4 k^3)
    M = m^2 (27 m^2 + 16 k^3),

and define

    g(z) = z (z + L)^2 - (8 k^2 z + M)^2 + D (A z + B)^2
    q(y) = g(y^2)
    P(x) = g((x^4 + 4 k x^2 + 4 m x)^2).

For the relative quartic x^4 + 4 k x^2 + 4 m x + y, the
product of its discriminants at y and -y is

    65536 ((8 k^2 y^2 + M)^2 - y^2 (y^2 + L)^2).

At roots of q this equals D * (256 (A y^2 + B))^2.

Thus unions of two of the three size-two quotient blocks have square
discriminant product.  When q has group 6T11, this places the degree-24
action in the index-four S4-wreath sign-code family containing 24T24877.

This script is only an arithmetic/profile pilot.  It does not stage or submit.
"""

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

from sage.all import GF, PolynomialRing, QQ, libgap, prime_range


ROOT = Path.cwd()
OUTPUT = ROOT / "data" / "p27_s4_wreath_6t11_pairnorm_pilot_20260727.jsonl"
SUMMARY = (
    ROOT / "data" / "p27_s4_wreath_6t11_pairnorm_pilot_20260727_summary.json"
)
TARGET_T = 24877
TARGET_R = {10, 14, 18}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--k-max", type=int, default=4)
    parser.add_argument("--k-only", type=int)
    parser.add_argument("--m-max", type=int, default=3)
    parser.add_argument("--m-only", type=int)
    parser.add_argument("--a-max", type=int, default=4)
    parser.add_argument("--b-max", type=int, default=8)
    parser.add_argument("--d-max", type=int, default=30)
    parser.add_argument("--prime-max", type=int, default=100)
    parser.add_argument("--max-valid-bases", type=int, default=500)
    parser.add_argument("--max-wall-seconds", type=int, default=900)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    return parser.parse_args()


def is_square_integer(value):
    if value < 0:
        return False
    root = math.isqrt(value)
    return root * root == value


def squarefree_part(value):
    value = int(value)
    if value == 0:
        return 0
    sign = -1 if value < 0 else 1
    value = abs(value)
    result = 1
    prime = 2
    while prime * prime <= value:
        exponent = 0
        while value % prime == 0:
            value //= prime
            exponent += 1
        if exponent % 2:
            result *= prime
        prime = 3 if prime == 2 else prime + 2
    if value > 1:
        result *= value
    return sign * result


def coefficient_line(polynomial):
    return ",".join(str(int(c)) for c in polynomial.list())


def coefficient_hash(polynomial):
    return hashlib.sha256(coefficient_line(polynomial).encode("utf-8")).hexdigest()


def cycle_type(polynomial, prime):
    reduced = polynomial.change_ring(GF(prime))
    if not reduced.is_squarefree():
        return None
    return tuple(
        sorted(
            degree
            for factor, multiplicity in reduced.factor()
            for degree in [int(factor.degree())] * int(multiplicity)
        )
    )


def target_cycle_types():
    group = libgap.TransitiveGroup(24, TARGET_T)
    points = list(range(1, 25))
    return {
        tuple(
            sorted(
                int(length)
                for length in libgap.CycleLengths(
                    libgap.Representative(conjugacy_class), points
                )
            )
        )
        for conjugacy_class in libgap.ConjugacyClasses(group)
    }


def exact_transitive_number(polynomial):
    group = polynomial.galois_group()
    return int(group.transitive_number())


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main():
    args = parse_args()
    started = time.monotonic()
    ring_z = PolynomialRing(QQ, "z")
    z = ring_z.gen()
    ring_y = PolynomialRing(QQ, "y")
    y = ring_y.gen()
    ring_x = PolynomialRing(QQ, "x")
    x = ring_x.gen()

    allowed_types = target_cycle_types()
    primes = list(prime_range(5, args.prime_max + 1))
    d_values = sorted(
        {
            squarefree_part(value)
            for value in range(-args.d_max, args.d_max + 1)
            if value not in (0, 1)
            and not is_square_integer(value)
            and squarefree_part(value) != 1
        },
        key=lambda value: (abs(value), value),
    )
    k_values = (
        [args.k_only]
        if args.k_only is not None
        else sorted(
            range(-args.k_max, args.k_max + 1),
            key=lambda value: (value == 0, abs(value), value),
        )
    )
    # Negative m applies x -> -x to P and cannot change the pair/signature.
    m_values = (
        [args.m_only]
        if args.m_only is not None
        else list(range(1, args.m_max + 1))
    )
    a_values = sorted(
        range(-args.a_max, args.a_max + 1),
        key=lambda value: (value == 0, abs(value), value),
    )
    b_values = sorted(
        range(-args.b_max, args.b_max + 1),
        key=lambda value: (abs(value), value),
    )

    counts = {
        "parameterTuples": 0,
        "distinctCubics": 0,
        "irreducibleS3Cubics": 0,
        "irreducibleSextics": 0,
        "exact6T11Bases": 0,
        "signatureCounts": {},
        "targetSignatureCandidates": 0,
        "irreducibleDegree24": 0,
        "targetProfileSurvivors": 0,
    }
    seen_cubics = set()
    seen_candidates = set()
    rows = []
    stopped = None

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for k in k_values:
            for m in m_values:
                ell = 4 * k * (9 * m**2 + 4 * k**3)
                em = m**2 * (27 * m**2 + 16 * k**3)
                for a_value in a_values:
                    for b_value in b_values:
                        if a_value == 0 and b_value == 0:
                            continue
                        for d_value in d_values:
                            counts["parameterTuples"] += 1
                            if time.monotonic() - started > args.max_wall_seconds:
                                stopped = "max_wall_seconds"
                                break

                            cubic = (
                                z * (z + ell) ** 2
                                - (8 * k**2 * z + em) ** 2
                                + d_value * (a_value * z + b_value) ** 2
                            )
                            cubic_key = tuple(cubic.list())
                            if cubic_key in seen_cubics:
                                continue
                            seen_cubics.add(cubic_key)
                            counts["distinctCubics"] += 1
                            if not cubic.is_irreducible():
                                continue
                            if exact_transitive_number(cubic) != 2:
                                continue
                            counts["irreducibleS3Cubics"] += 1

                            sextic = ring_y(cubic(y**2))
                            if not sextic.is_irreducible():
                                continue
                            counts["irreducibleSextics"] += 1
                            if exact_transitive_number(sextic) != 11:
                                continue
                            counts["exact6T11Bases"] += 1
                            if counts["exact6T11Bases"] > args.max_valid_bases:
                                stopped = "max_valid_bases"
                                break

                            u = x**4 + 4 * k * x**2 + 4 * m * x
                            candidate = ring_x(cubic(u**2))
                            candidate_key = tuple(candidate.list())
                            if candidate_key in seen_candidates:
                                continue
                            seen_candidates.add(candidate_key)
                            real_roots = int(candidate.number_of_real_roots())
                            signature_key = str(real_roots)
                            counts["signatureCounts"][signature_key] = (
                                counts["signatureCounts"].get(signature_key, 0)
                                + 1
                            )
                            if real_roots not in TARGET_R:
                                continue
                            counts["targetSignatureCandidates"] += 1
                            if not candidate.is_irreducible():
                                continue
                            counts["irreducibleDegree24"] += 1

                            observed = []
                            bad_profile = None
                            for prime in primes:
                                profile = cycle_type(candidate, int(prime))
                                if profile is None:
                                    continue
                                observed.append([int(prime), list(profile)])
                                if profile not in allowed_types:
                                    bad_profile = [int(prime), list(profile)]
                                    break
                            if bad_profile is not None:
                                continue
                            counts["targetProfileSurvivors"] += 1
                            row = {
                                "k": int(k),
                                "m": int(m),
                                "A": int(a_value),
                                "B": int(b_value),
                                "D": int(d_value),
                                "cubic": coefficient_line(cubic),
                                "sextic": coefficient_line(sextic),
                                "polynomial": coefficient_line(candidate),
                                "coefficientSha256": coefficient_hash(candidate),
                                "r": real_roots,
                                "observedFrobenius": observed,
                                "structuralContainment": {
                                    "quotient": "6T11",
                                    "fiber": "S4",
                                    "signRelations": (
                                        "products over unions of any two "
                                        "size-two quotient blocks are squares"
                                    ),
                                    "candidateTarget": "24T24877",
                                },
                            }
                            rows.append(row)
                            stream.write(
                                json.dumps(
                                    row,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                                + "\n"
                            )
                            stream.flush()
                        if stopped:
                            break
                    if stopped:
                        break
                if stopped:
                    break
            if stopped:
                break

    summary = {
        "schemaVersion": "p27-s4-wreath-6t11-pairnorm-pilot-v2",
        "identity": {
            "L": "4*k*(9*m^2+4*k^3)",
            "M": "m^2*(27*m^2+16*k^3)",
            "g": "z*(z+L)^2-(8*k^2*z+M)^2+D*(A*z+B)^2",
            "q": "g(y^2)",
            "P": "g((x^4+4*k*x^2+4*m*x)^2)",
            "pairedDiscriminantNorm": (
                "D*(256*(A*y^2+B))^2 modulo q(y)"
            ),
        },
        "target": {"label": "24T24877", "r": sorted(TARGET_R)},
        "bounds": {
            "kMax": args.k_max,
            "kOnly": args.k_only,
            "mMax": args.m_max,
            "mOnly": args.m_only,
            "aMax": args.a_max,
            "bMax": args.b_max,
            "dMax": args.d_max,
            "primeMax": args.prime_max,
            "maxValidBases": args.max_valid_bases,
            "maxWallSeconds": args.max_wall_seconds,
        },
        "counts": counts,
        "survivors": [
            {
                "coefficientSha256": row["coefficientSha256"],
                "r": row["r"],
                "k": row["k"],
                "m": row["m"],
                "A": row["A"],
                "B": row["B"],
                "D": row["D"],
            }
            for row in rows
        ],
        "stopped": stopped,
        "elapsedSeconds": time.monotonic() - started,
        "output": str(args.output),
        "networkCalls": 0,
        "submissionCalls": 0,
        "warning": (
            "Profile survival plus structural containment is not yet an exact "
            "24T24877 identification; no row is stageable without the "
            "subgroup-exclusion certificate."
        ),
    }
    atomic_json(args.summary, summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
