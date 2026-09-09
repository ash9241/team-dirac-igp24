#!/usr/bin/env sage
"""Prescribed-cubic-resolvent S4 lifts over exact 6T13 sextic fields.

Let K=Q(y) be a sextic field with normal-closure group 6T13.  Over K, the
defining sextic factors as degrees 1, 2, and 3.  The degree-three factor is
the natural opposite cubic; its discriminant squareclass is present in the
normal closure of K but not normally in K itself.

For delta equal to that discriminant squareclass and parameters s,t in K,
put

    u = (t^2 + 3*delta*s^2)/4,
    v = (t^2 - 3*delta*s^2)/2,
    C(Z) = Z^3 - 3*u^2*Z - u^2*v.

Then Disc(C) = delta * (9*u^2*s*t)^2.  Thus C has the prescribed quadratic
resolvent while its cyclic cubic part can vary.  Turning C into a quartic by
the classical square-root construction and taking Norm_{K/Q} gives a
degree-24 S4-over-6T13 field.  The expected kernels are V4^6 times a cubic
module of rank 0, 2, 3, or 4; multiplying delta by a rational nonsquare adds
the shared sign bit seen in the remaining target groups.

This pilot performs exact arithmetic and Frobenius profile matching only.  It
does not stage or submit candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from functools import reduce
from math import gcd, lcm
from pathlib import Path

from sage.all import GF, NumberField, PolynomialRing, QQ, ZZ, pari, prime_range


ROOT = Path(__file__).resolve().parent
DEFAULT_BASES = ROOT / "data" / "agent_non12_recoverable_subfields.jsonl"
DEFAULT_TARGETS = ROOT / "data" / "current_rank11_alex_s4_resolvent_actions_20260814.json"
DEFAULT_OUTPUT = ROOT / "data" / "current_rank11_s4_q13_resolvent_pilot_20260814.jsonl"
DEFAULT_SUMMARY = ROOT / "data" / "current_rank11_s4_q13_resolvent_pilot_20260814_summary.json"


def coefficient_line(polynomial):
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def primitive_monic(polynomial, ring):
    polynomial = ring(polynomial)
    denominator = ZZ(polynomial.denominator())
    coefficients = [ZZ(denominator * value) for value in polynomial.list()]
    content = reduce(gcd, (abs(int(value)) for value in coefficients if value), 0)
    if content:
        coefficients = [value // content for value in coefficients]
    integral = ring(coefficients)
    if integral.leading_coefficient() < 0:
        integral = -integral
    if integral.leading_coefficient() != 1:
        return None
    return integral


def integral_monic_scale(polynomial, ring):
    """Scale a monic rational generator to an algebraic integer."""
    polynomial = ring(polynomial)
    if polynomial.leading_coefficient() != 1:
        polynomial = polynomial / polynomial.leading_coefficient()
    degree = int(polynomial.degree())
    denominator = 1
    for coefficient in polynomial.list():
        denominator = lcm(denominator, int(QQ(coefficient).denominator()))
    coefficients = []
    for index, coefficient in enumerate(polynomial.list()):
        value = QQ(coefficient) * denominator ** (degree - index)
        if value.denominator() != 1:
            raise ArithmeticError("integral generator scaling failed")
        coefficients.append(ZZ(value))
    candidate = ring(coefficients)
    if candidate.degree() != degree or candidate.leading_coefficient() != 1:
        raise ArithmeticError("scaled polynomial is not monic of the expected degree")
    return candidate


def cycle_type(polynomial, prime):
    reduced = polynomial.change_ring(GF(prime))
    if not reduced.is_squarefree():
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in reduced.factor()
            for _ in range(int(exponent))
        )
    )


def load_bases(path, limit, offset, quotient_label):
    rows = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        group = row.get("galoisGroup", {})
        if str(group.get("label")) != quotient_label or int(row.get("subfieldDegree", -1)) != 6:
            continue
        coefficients = str(row["coefficientLine"])
        if coefficients in seen:
            continue
        seen.add(coefficients)
        if len(seen) <= offset:
            continue
        rows.append(
            {
                "coefficientLine": coefficients,
                "coefficientSha256": str(row["coefficientSha256"]),
                "realRoots": int(row["realRoots"]),
                "fieldDiscriminantAbs": str(row["fieldDiscriminantAbs"]),
                "sourceLabel": str(row["sourceLabel"]),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def load_targets(path, quotient_label):
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "label": str(row["label"]),
            "pairCount": int(row["pairCount"]),
            "possibleRealRoots": {int(value) for value in row["possibleRealRoots"]},
            "cycleProfiles": {
                tuple(int(value) for value in profile) for profile in row["cycleProfiles"]
            },
            "blockKernelOrder": int(row["system"]["blockKernelOrder"]),
            "resolventIndex": int(row["system"]["resolventIndexOverBlockQuotient"]),
            "resolventActionLabel": str(row["system"]["resolventActionLabel"]),
            "resolventCycleProfiles": {
                tuple(int(value) for value in profile)
                for profile in row["system"]["resolventCycleProfiles"]
            },
            "properTransitiveMaximals": [
                {
                    "label": str(maximal["label"]),
                    "order": int(maximal["order"]),
                    "cycleProfiles": {
                        tuple(int(value) for value in profile)
                        for profile in maximal["cycleProfiles"]
                    },
                }
                for maximal in row["properTransitiveMaximals"]
            ],
        }
        for row in payload["targets"]
        if str(row["system"]["quotientLabel"]) == quotient_label
    ]


def small_forms(generator, bound, seed=None, extra=0):
    values = [QQ(1)]
    for c in range(-bound, bound + 1):
        values.append(generator + c)
        values.append(generator**2 + c)
    # A few asymmetric two-term forms materially change the cubic module.
    values.extend(
        [
            1 + generator,
            1 - generator,
            1 + generator**2,
            1 - generator**2,
            generator + generator**2,
            generator - generator**2,
        ]
    )
    if extra:
        rng = random.Random(seed)
        while len(values) < extra + 2 * (2 * bound + 1) + 7:
            coefficients = [rng.randint(-bound, bound) for _ in range(6)]
            if sum(value != 0 for value in coefficients) < 2:
                continue
            values.append(
                sum(
                    QQ(coefficient) * generator**degree
                    for degree, coefficient in enumerate(coefficients)
                )
            )
    unique = []
    seen = set()
    for value in values:
        if value == 0:
            continue
        key = str(value)
        if key not in seen:
            seen.add(key)
            unique.append(value)
    if seed is not None:
        random.Random(seed).shuffle(unique)
    return unique


def quartic_from_cubic(cubic, quartic_ring):
    if cubic.degree() != 3 or cubic.leading_coefficient() != 1:
        raise ValueError("expected monic cubic")
    e1 = -cubic[2]
    e2 = cubic[1]
    e3 = -cubic[0]
    if e3 == 0:
        return None
    scale = e3
    scaled_e1 = scale * e1
    scaled_e2 = scale**2 * e2
    square_root_e3 = e3**2
    X = quartic_ring.gen()
    return (
        X**4
        - 2 * scaled_e1 * X**2
        - 8 * square_root_e3 * X
        + scaled_e1**2
        - 4 * scaled_e2
    )


def element_in_bivariate(value, y, bivariate):
    representative = value.polynomial()
    return bivariate(
        sum(QQ(coefficient) * y**index for index, coefficient in enumerate(representative.list()))
    )


def norm_polynomial(
    q_coefficients, relative, generator, bivariate, absolute_ring, expected_degree
):
    x, y = bivariate.gens()
    q_y = sum(QQ(value) * y**index for index, value in enumerate(q_coefficients))
    relative_xy = sum(
        element_in_bivariate(relative[index], y, bivariate) * x**index
        for index in range(relative.degree() + 1)
    )
    # A root of the raw norm need not generate K.  The primitive-element
    # shifts alpha+c*y preserve the field and generically have degree 24.
    for shift in (0, 1, -1, 2, -2, 3, -3):
        shifted = relative_xy(x=x - shift * y, y=y)
        eliminated = absolute_ring(q_y.resultant(shifted, y))
        if eliminated.degree() != expected_degree:
            continue
        candidate = integral_monic_scale(eliminated, absolute_ring)
        if candidate.is_irreducible():
            return candidate, shift
    return None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bases", type=Path, default=DEFAULT_BASES)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--base-limit", type=int, default=8)
    parser.add_argument("--base-offset", type=int, default=0)
    parser.add_argument("--quotient-label", default="6T13")
    parser.add_argument(
        "--delta-factor-ordinal",
        type=int,
        default=0,
        help="which natural degree-2 factor supplies delta when no degree-3 factor exists",
    )
    parser.add_argument(
        "--constant-delta",
        type=int,
        help="override the natural orbit discriminant with this rational squareclass",
    )
    parser.add_argument("--form-bound", type=int, default=2)
    parser.add_argument(
        "--form-seed",
        type=int,
        help="deterministically shuffle forms; also seeds --form-extra generation",
    )
    parser.add_argument(
        "--form-extra",
        type=int,
        default=0,
        help="add this many dense degree-at-most-5 forms before shuffling",
    )
    parser.add_argument(
        "--generic-candidates",
        type=int,
        default=0,
        help="try this many deterministic generic quartics over each sextic base",
    )
    parser.add_argument(
        "--generic-seed",
        type=int,
        default=1,
        help="seed for generic quartic coefficient selection",
    )
    parser.add_argument(
        "--generic-only",
        action="store_true",
        help="skip prescribed-resolvent jobs and run only generic quartics",
    )
    parser.add_argument(
        "--cubic-shift",
        type=int,
        default=0,
        help="translate each constructed cubic by Z -> Z-c before the quartic lift",
    )
    parser.add_argument("--prime-max", type=int, default=251)
    parser.add_argument("--max-candidates", type=int, default=500)
    parser.add_argument("--max-wall-seconds", type=int, default=3600)
    parser.add_argument(
        "--skip-reduction",
        action="store_true",
        help="retain the integral primitive-element polynomial instead of PARI polredbest",
    )
    parser.add_argument(
        "--twists",
        default="1,-1,2,-2,3,-3,5,-5",
        help="comma-separated rational discriminant twists",
    )
    parser.add_argument(
        "--element-twist",
        default="1",
        help="comma-separated coefficients of a sextic-base element multiplying delta",
    )
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite output")

    targets = load_targets(args.targets, args.quotient_label)
    bases = load_bases(
        args.bases, args.base_limit, args.base_offset, args.quotient_label
    )
    if not targets:
        raise ValueError(f"no S4 targets for {args.quotient_label}")
    if not bases:
        raise ValueError(f"no certified sextic bases for {args.quotient_label}")
    twists = [ZZ(value) for value in args.twists.split(",") if value.strip()]
    element_twist_coefficients = [
        ZZ(value) for value in args.element_twist.split(",") if value.strip()
    ]
    if not element_twist_coefficients or not any(element_twist_coefficients):
        raise ValueError("--element-twist must define a nonzero base-field element")
    primes = [int(value) for value in prime_range(5, args.prime_max + 1)]
    rational_ring = PolynomialRing(QQ, "Y")
    Y = rational_ring.gen()
    bivariate = PolynomialRing(QQ, names=("x", "y"))
    absolute_ring = PolynomialRing(QQ, "x")
    started = time.monotonic()
    rows = []
    seen = set()
    counts = {
        "bases": len(bases),
        "attempts": 0,
        "relativeIrreducible": 0,
        "degree24Monic": 0,
        "absoluteIrreducible": 0,
        "profileCompatible": 0,
        "uniqueProfileMatches": 0,
        "exactTargetCertified": 0,
        "signatureCounts": {},
        "familyCounts": {},
    }
    stopped = None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for base_index, base in enumerate(bases):
            q_coefficients = [ZZ(value) for value in base["coefficientLine"].split(",")]
            q = rational_ring(q_coefficients)
            K = NumberField(q, "a")
            a = K.gen()
            cubic_ring = PolynomialRing(K, "Z")
            Z = cubic_ring.gen()
            element_twist = sum(
                coefficient * a**degree
                for degree, coefficient in enumerate(element_twist_coefficients)
            )
            factorization = cubic_ring(q).factor()
            natural_cubics = [
                factor.monic()
                for factor, exponent in factorization
                if factor.degree() == 3 and int(exponent) == 1
            ]
            natural_quadratics = [
                factor.monic()
                for factor, exponent in factorization
                if factor.degree() == 2 and int(exponent) == 1
            ]
            natural_quartics = [
                factor.monic()
                for factor, exponent in factorization
                if factor.degree() == 4 and int(exponent) == 1
            ]
            natural = None
            if args.constant_delta is not None:
                delta = K(args.constant_delta)
            elif len(natural_cubics) == 1:
                natural = natural_cubics[0]
                delta = natural.discriminant()
            elif natural_quadratics:
                ordinal = args.delta_factor_ordinal % len(natural_quadratics)
                delta = natural_quadratics[ordinal].discriminant()
            elif natural_quartics:
                ordinal = args.delta_factor_ordinal % len(natural_quartics)
                delta = natural_quartics[ordinal].discriminant()
            else:
                raise ArithmeticError(
                    f"base {base_index}: no natural cubic/quadratic/quartic delta factor: {factorization}"
                )
            quartic_ring = PolynomialRing(K, "X")
            forms = small_forms(
                a,
                args.form_bound,
                seed=args.form_seed,
                extra=args.form_extra,
            )
            jobs = []
            if not args.generic_only:
                if natural is not None:
                    jobs.append(("natural", ZZ(1), QQ(1), QQ(1), natural, None))
                for twist in twists:
                    twisted_delta = K(twist) * element_twist * delta
                    for s in forms:
                        for t in forms:
                            u = (t**2 + 3 * twisted_delta * s**2) / 4
                            v = (t**2 - 3 * twisted_delta * s**2) / 2
                            prescribed = Z**3 - 3 * u**2 * Z - u**2 * v
                            claimed_square = (9 * u**2 * s * t) ** 2
                            if prescribed.discriminant() != twisted_delta * claimed_square:
                                raise ArithmeticError("prescribed discriminant identity failed")
                            jobs.append(("prescribed", twist, s, t, prescribed, None))
            generic_rng = random.Random(args.generic_seed + base_index)
            X = quartic_ring.gen()
            for _ in range(args.generic_candidates):
                quartic_coefficients = [generic_rng.choice(forms) for _ in range(4)]
                qa, qb, qc, qd = quartic_coefficients
                generic_quartic = X**4 + qa * X**3 + qb * X**2 + qc * X + qd
                generic_resolvent = (
                    Z**3
                    - qb * Z**2
                    + (qa * qc - 4 * qd) * Z
                    + (4 * qb * qd - qa**2 * qd - qc**2)
                )
                jobs.append(
                    (
                        "generic",
                        ZZ(0),
                        tuple(quartic_coefficients),
                        QQ(0),
                        generic_resolvent,
                        generic_quartic,
                    )
                )

            for family, twist, s, t, cubic, relative in jobs:
                if counts["attempts"] >= args.max_candidates:
                    stopped = "max_candidates"
                    break
                if time.monotonic() - started > args.max_wall_seconds:
                    stopped = "max_wall_seconds"
                    break
                counts["attempts"] += 1
                counts["familyCounts"][family] = counts["familyCounts"].get(family, 0) + 1
                if relative is None:
                    if args.cubic_shift:
                        cubic = cubic(Z - args.cubic_shift).monic()
                    relative = quartic_from_cubic(cubic.monic(), quartic_ring)
                if relative is None or not relative.is_irreducible():
                    continue
                counts["relativeIrreducible"] += 1
                resolvent_candidate, resolvent_shift = norm_polynomial(
                    q_coefficients,
                    cubic.monic(),
                    a,
                    bivariate,
                    absolute_ring,
                    18,
                )
                if resolvent_candidate is None:
                    continue
                candidate, primitive_shift = norm_polynomial(
                    q_coefficients,
                    relative,
                    a,
                    bivariate,
                    absolute_ring,
                    24,
                )
                if candidate is None or candidate.degree() != 24:
                    continue
                counts["degree24Monic"] += 1
                if not candidate.is_irreducible():
                    continue
                counts["absoluteIrreducible"] += 1
                real_roots = int(candidate.number_of_real_roots())
                counts["signatureCounts"][str(real_roots)] = (
                    counts["signatureCounts"].get(str(real_roots), 0) + 1
                )
                compatible = [
                    target for target in targets if real_roots in target["possibleRealRoots"]
                ]
                compatible_resolvents = list(targets)
                observed = []
                observed_resolvent = []
                for prime in primes:
                    profile = cycle_type(candidate, prime)
                    resolvent_profile = cycle_type(resolvent_candidate, prime)
                    if profile is not None:
                        observed.append([prime, list(profile)])
                        compatible = [
                            target
                            for target in compatible
                            if profile in target["cycleProfiles"]
                        ]
                    if resolvent_profile is not None:
                        observed_resolvent.append([prime, list(resolvent_profile)])
                        compatible_resolvents = [
                            target
                            for target in compatible_resolvents
                            if resolvent_profile in target["resolventCycleProfiles"]
                        ]
                    if not compatible or not compatible_resolvents:
                        break
                if not compatible or not compatible_resolvents:
                    continue
                if args.skip_reduction:
                    reduced_candidate = candidate
                else:
                    try:
                        reduced_candidate = absolute_ring(pari(candidate).polredbest())
                    except Exception:
                        reduced_candidate = candidate
                if (
                    reduced_candidate.degree() != 24
                    or reduced_candidate.leading_coefficient() != 1
                    or not reduced_candidate.is_irreducible()
                ):
                    continue
                line = coefficient_line(reduced_candidate)
                digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                inferred = (
                    compatible_resolvents[0]
                    if len(compatible_resolvents) == 1
                    else None
                )
                maximal_certificate = []
                exact = False
                if inferred is not None and inferred in compatible:
                    for maximal in inferred["properTransitiveMaximals"]:
                        witness = next(
                            (
                                {"prime": prime, "cycleType": profile}
                                for prime, profile in observed
                                if tuple(profile) not in maximal["cycleProfiles"]
                            ),
                            None,
                        )
                        maximal_certificate.append(
                            {
                                "label": maximal["label"],
                                "order": maximal["order"],
                                "witness": witness,
                            }
                        )
                    exact = bool(maximal_certificate) and all(
                        row["witness"] is not None for row in maximal_certificate
                    )
                if exact:
                    counts["exactTargetCertified"] += 1
                counts["profileCompatible"] += 1
                if len(compatible) == 1:
                    counts["uniqueProfileMatches"] += 1
                row = {
                    "baseIndex": base_index,
                    "base": base,
                    "coefficientSha256": digest,
                    "compatibleTargets": [
                        {
                            "label": target["label"],
                            "pairCount": target["pairCount"],
                            "blockKernelOrder": target["blockKernelOrder"],
                            "resolventIndex": target["resolventIndex"],
                        }
                        for target in compatible
                    ],
                    "constructionFamily": family,
                    "cubicShift": args.cubic_shift,
                    "discriminantTwist": int(twist),
                    "elementTwistCoefficients": [
                        int(value) for value in element_twist_coefficients
                    ],
                    "exactConclusion": inferred["label"] if exact else None,
                    "exactTargetCertified": exact,
                    "inferredResolventTargets": [
                        {
                            "label": target["label"],
                            "resolventActionLabel": target["resolventActionLabel"],
                            "resolventIndex": target["resolventIndex"],
                        }
                        for target in compatible_resolvents
                    ],
                    "maximalSubgroupCertificate": maximal_certificate,
                    "observedFrobenius": observed,
                    "observedResolventFrobenius": observed_resolvent,
                    "polynomial": line,
                    "primitiveElementShift": primitive_shift,
                    "r": real_roots,
                    "resolventPolynomial": coefficient_line(resolvent_candidate),
                    "resolventPrimitiveElementShift": resolvent_shift,
                    "s": str(s),
                    "tParameter": str(t),
                }
                rows.append(row)
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                stream.flush()
            if stopped:
                break

    summary = {
        "schemaVersion": "rank11-s4-q13-prescribed-resolvent-pilot-v1",
        "bounds": {
            "baseLimit": args.base_limit,
            "baseOffset": args.base_offset,
            "formBound": args.form_bound,
            "formSeed": args.form_seed,
            "formExtra": args.form_extra,
            "genericCandidates": args.generic_candidates,
            "genericSeed": args.generic_seed,
            "genericOnly": bool(args.generic_only),
            "cubicShift": args.cubic_shift,
            "primeMax": args.prime_max,
            "maxCandidates": args.max_candidates,
            "maxWallSeconds": args.max_wall_seconds,
            "twists": [int(value) for value in twists],
            "elementTwistCoefficients": [
                int(value) for value in element_twist_coefficients
            ],
            "skipReduction": bool(args.skip_reduction),
            "quotientLabel": args.quotient_label,
            "deltaFactorOrdinal": args.delta_factor_ordinal,
            "constantDelta": args.constant_delta,
        },
        "counts": counts,
        "elapsedSeconds": time.monotonic() - started,
        "resultRows": len(rows),
        "stopped": stopped,
        "output": str(args.output),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    temporary = args.summary.with_suffix(args.summary.suffix + ".tmp")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
