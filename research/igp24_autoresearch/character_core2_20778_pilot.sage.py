#!/usr/bin/env sage -python
"""Offline, bounded character-lift pilot for live target 24T20778/r24.

This script never calls the network and never submits anything.  It recovers an
exact, totally real 12T140 base from the local verified ledger, searches a
user-bounded S-unit squareclass space for a totally positive element ``h`` with

    Norm(h) = 2 * square,

and constructs ``P(x) = minpoly(h)(x^2)``.  Every candidate is checked for
integrality, irreducibility, and 24 real roots.  Equality with 24T20778 is then
certified by containment in the exact character lift plus joint Frobenius
witnesses against every proper transitive maximal subgroup.

With no ``--max-candidates`` argument the script performs only the cheap local
provenance and GAP audit.  Search is deliberately opt-in and bounded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
from pathlib import Path

from sage.all import (
    GF,
    Matrix,
    NumberField,
    PolynomialRing,
    QQ,
    RealField,
    ZZ,
    kronecker,
    libgap,
    lcm,
    primes_first_n,
    prod,
    vector,
)


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"

BASE_ROW = ("sub_60ebcccea7004efea1c78c5d47af3168", 425)
CORE_229_ROW = ("sub_ed753f5286004e2db28643c5c3e9b31a", 178)
CORE_687_ROW = ("sub_b34d3ffa621c4200a0d1c7e0e690291d", 29)
CORE_3_ROW = ("sub_ce6cc49ade964e15a5389bdf9ddcf674", 92)

EXPECTED = {
    BASE_ROW: ("24T20777", 12, 1),
    CORE_229_ROW: ("24T20774", 12, 229),
    CORE_687_ROW: ("24T20776", 12, 687),
    CORE_3_ROW: ("24T20779", 8, 3),
}

EXPECTED_BASE_DISC = ZZ(311433694751490048)
QUOTIENT_T = 140
TARGET_T = 20778
TARGET_LABEL = "24T20778"


def coefficient_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    values += [ZZ(0)] * (25 - len(values))
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not monic of degree 24")
    return ",".join(str(value) for value in values)


def load_even_ledger_polynomial(
    connection: sqlite3.Connection, key: tuple[str, int]
):
    row = connection.execute(
        """
        SELECT p.coefficients,v.label,v.r,v.scoreable,v.field_disc_abs
        FROM polynomials AS p
        JOIN verifications AS v USING (submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        key,
    ).fetchone()
    if row is None:
        raise ValueError(f"missing ledger row {key}")
    coefficients = [ZZ(value) for value in str(row[0]).split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError(f"bad degree-24 row {key}")
    if any(coefficients[index] for index in range(1, 25, 2)):
        raise ValueError(f"row {key} is not of the form q(x^2)")
    expected_label, expected_r, expected_core = EXPECTED[key]
    if str(row[1]) != expected_label or int(row[2]) != expected_r or int(row[3]) != 1:
        raise ValueError(f"ledger provenance mismatch for {key}")
    ring = PolynomialRing(ZZ, "y")
    quotient = ring(coefficients[::2])
    core = ZZ(quotient[0]).squarefree_part()
    if core != expected_core:
        raise ValueError(f"norm-core mismatch for {key}: {core} != {expected_core}")
    return {
        "fieldDiscAbs": str(row[4]) if row[4] else None,
        "key": {"submissionId": key[0], "polynomialIndex": key[1]},
        "label": str(row[1]),
        "quotient": quotient,
        "r": int(row[2]),
        "squarefreeNormCore": int(core),
    }


def block_character_kernel(target_t: int, standard_quotient):
    """Return the parity character kernel inside standard 12T140."""
    group = libgap.TransitiveGroup(24, target_t)
    two_blocks = [
        block for block in libgap.AllBlocks(group) if int(libgap.Length(block)) == 2
    ]
    if not two_blocks:
        raise ValueError(f"24T{target_t} has no two-point block system")
    blocks = libgap.Orbit(group, two_blocks[0], libgap.OnSets)
    block_rows = [sorted(int(value) for value in block) for block in blocks]
    block_position = {tuple(block): index for index, block in enumerate(block_rows)}
    generators = list(libgap.GeneratorsOfGroup(group))
    parity_values = []
    for generator in generators:
        parity = 0
        for block in block_rows:
            image = [int(libgap.OnPoints(value, generator)) for value in block]
            target = block_rows[block_position[tuple(sorted(image))]]
            parity ^= image[0] == target[1]
        parity_values.append(int(parity))

    c2 = libgap.Group(libgap.eval("(1,2)"))
    c2_generator = list(libgap.GeneratorsOfGroup(c2))[0]
    parity_hom = libgap.GroupHomomorphismByImages(
        group,
        c2,
        generators,
        [c2_generator if value else libgap.One(c2) for value in parity_values],
    )
    if parity_hom == libgap.fail:
        raise ValueError(f"parity values do not define a character for 24T{target_t}")

    block_action = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
    quotient = libgap.Image(block_action)
    if int(libgap.TransitiveIdentification(quotient)) != QUOTIENT_T:
        raise ValueError(f"24T{target_t} does not have quotient 12T{QUOTIENT_T}")
    conjugator = libgap.RepresentativeAction(
        libgap.SymmetricGroup(12), quotient, standard_quotient
    )
    if conjugator == libgap.fail:
        raise ValueError("could not align the block quotient with standard 12T140")
    parity_kernel = libgap.Image(block_action, libgap.Kernel(parity_hom))
    return libgap.OnPoints(parity_kernel, conjugator)


def product_character_kernel(quotient, first_kernel, second_kernel):
    generators = list(libgap.GeneratorsOfGroup(quotient))
    values = [
        (0 if generator in first_kernel else 1)
        ^ (0 if generator in second_kernel else 1)
        for generator in generators
    ]
    c2 = libgap.Group(libgap.eval("(1,2)"))
    c2_generator = list(libgap.GeneratorsOfGroup(c2))[0]
    homomorphism = libgap.GroupHomomorphismByImages(
        quotient,
        c2,
        generators,
        [c2_generator if value else libgap.One(c2) for value in values],
    )
    if homomorphism == libgap.fail:
        raise ValueError("failed to multiply quotient characters")
    return libgap.Kernel(homomorphism)


def factor_degrees(polynomial, prime: int) -> tuple[int, ...] | None:
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


def character_alignment_certificate(base_quotient):
    """Prove that arithmetic core 2 lands in the 24T20778 character orbit."""
    quotient = libgap.TransitiveGroup(12, QUOTIENT_T)
    points = libgap.eval("[1..12]")
    kernel_229 = block_character_kernel(20774, quotient)
    kernel_3 = block_character_kernel(20779, quotient)
    kernel_c = block_character_kernel(20778, quotient)
    kernel_3c = product_character_kernel(quotient, kernel_3, kernel_c)

    normalizer = libgap.Normalizer(libgap.SymmetricGroup(12), quotient)
    conjugator = libgap.RepresentativeAction(normalizer, kernel_c, kernel_3c)
    if conjugator == libgap.fail:
        raise ValueError("characters c and 3*c are not in the same target orbit")

    class_profiles = []
    for conjugacy_class in libgap.ConjugacyClasses(quotient):
        representative = libgap.Representative(conjugacy_class)
        cycle_type = tuple(
            sorted(int(value) for value in libgap.CycleLengths(representative, points))
        )
        class_profiles.append(
            (
                cycle_type,
                0 if representative in kernel_229 else 1,
                0 if representative in kernel_3 else 1,
                0 if representative in kernel_c else 1,
            )
        )

    equations = []
    discriminant = ZZ(base_quotient.discriminant())
    for prime in primes_first_n(300):
        prime = int(prime)
        if discriminant % prime == 0:
            continue
        cycle_type = factor_degrees(base_quotient, prime)
        value_229 = 0 if kronecker(229, prime) == 1 else 1
        value_3 = 0 if kronecker(3, prime) == 1 else 1
        value_2 = 0 if kronecker(2, prime) == 1 else 1
        possible_c = {
            c_value
            for profile, a_value, b_value, c_value in class_profiles
            if profile == cycle_type
            and a_value == value_229
            and b_value == value_3
        }
        if len(possible_c) == 1:
            equations.append(
                (prime, cycle_type, value_229, value_3, value_2, possible_c.pop())
            )

    offsets = []
    for add_229 in (0, 1):
        for add_3 in (0, 1):
            if all(
                value_2
                == (c_value ^ (add_229 & value_229) ^ (add_3 & value_3))
                for _prime, _cycle, value_229, value_3, value_2, c_value in equations
            ):
                offsets.append((add_229, add_3))
    if set(offsets) != {(0, 0), (0, 1)}:
        raise ValueError(f"unexpected core-2 character offsets: {offsets}")

    return {
        "arithmeticCore2OffsetsFromC": [list(value) for value in offsets],
        "characterEquations": len(equations),
        "conjugateTargetCharacters": ["c", "3*c"],
        "normalizerOrder": int(libgap.Size(normalizer)),
        "normalizerConjugator": str(conjugator),
        "targetLabel": TARGET_LABEL,
    }


def target_structure_certificate():
    group = libgap.TransitiveGroup(24, TARGET_T)
    two_blocks = [
        block for block in libgap.AllBlocks(group) if int(libgap.Length(block)) == 2
    ]
    blocks = libgap.Orbit(group, two_blocks[0], libgap.OnSets)
    action = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
    quotient = libgap.Image(action)
    return {
        "blockCount": int(libgap.Length(blocks)),
        "blockKernelOrder": int(libgap.Size(libgap.Kernel(action))),
        "groupOrder": int(libgap.Size(group)),
        "quotientOrder": int(libgap.Size(quotient)),
        "quotientT": int(libgap.TransitiveIdentification(quotient)),
        "targetLabel": TARGET_LABEL,
    }


def maximal_joint_profiles():
    group = libgap.TransitiveGroup(24, TARGET_T)
    points_24 = libgap.eval("[1..24]")
    two_blocks = [
        block for block in libgap.AllBlocks(group) if int(libgap.Length(block)) == 2
    ]
    blocks = libgap.Orbit(group, two_blocks[0], libgap.OnSets)
    quotient_action = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
    points_12 = libgap.eval("[1..12]")
    maximal_subgroups = [
        subgroup
        for subgroup in libgap.MaximalSubgroupClassReps(group)
        if libgap.IsTransitive(subgroup, points_24)
    ]
    profiles = []
    identities = []
    for subgroup in maximal_subgroups:
        subgroup_profiles = set()
        for conjugacy_class in libgap.ConjugacyClasses(subgroup):
            representative = libgap.Representative(conjugacy_class)
            action_24 = tuple(
                sorted(
                    int(value)
                    for value in libgap.CycleLengths(representative, points_24)
                )
            )
            action_12 = tuple(
                sorted(
                    int(value)
                    for value in libgap.CycleLengths(
                        libgap.Image(quotient_action, representative), points_12
                    )
                )
            )
            subgroup_profiles.add((action_24, action_12))
        profiles.append(subgroup_profiles)
        identities.append(
            {
                "label": f"24T{int(libgap.TransitiveIdentification(subgroup))}",
                "order": int(libgap.Size(subgroup)),
            }
        )
    return profiles, identities


def frobenius_maximal_certificate(
    candidate, base_quotient, profiles, identities, maximum_prime_count: int
):
    witnesses = [None] * len(profiles)
    checked = 0
    for prime in primes_first_n(maximum_prime_count):
        prime = int(prime)
        candidate_type = factor_degrees(candidate, prime)
        quotient_type = factor_degrees(base_quotient, prime)
        if candidate_type is None or quotient_type is None:
            continue
        checked += 1
        joint_profile = (candidate_type, quotient_type)
        for index, profile in enumerate(profiles):
            if witnesses[index] is None and joint_profile not in profile:
                witnesses[index] = {
                    "candidateCycleType": list(candidate_type),
                    "prime": prime,
                    "quotientCycleType": list(quotient_type),
                }
        if all(witness is not None for witness in witnesses):
            break
    rows = []
    for identity, witness in zip(identities, witnesses):
        rows.append({**identity, "witness": witness})
    return {
        "checkedSquarefreePrimes": checked,
        "complete": all(witness is not None for witness in witnesses),
        "properTransitiveMaximals": rows,
    }


def state_bits(sign_mask: int, norm, rational_primes: list[int]) -> list[int]:
    result = [(sign_mask >> index) & 1 for index in range(12)]
    rational_norm = QQ(norm)
    result.extend(int(rational_norm.valuation(prime)) & 1 for prime in rational_primes)
    return result


def vector_to_mask(value) -> int:
    return sum((1 << index) for index, bit in enumerate(value) if int(bit))


def affine_solutions(matrix, target, maximum: int, seed: int):
    particular = matrix.solve_right(target)
    kernel_basis = list(matrix.right_kernel().basis())
    choices = [0]
    choices.extend(1 << index for index in range(len(kernel_basis)))
    generator = random.Random(seed)
    while len(choices) < maximum:
        choices.append(generator.getrandbits(len(kernel_basis)))
    seen = set()
    for choice in choices:
        # Sage vectors are mutable: start each affine trial from a fresh copy.
        solution = vector(GF(2), particular)
        for index, basis in enumerate(kernel_basis):
            if (choice >> index) & 1:
                solution += basis
        mask = vector_to_mask(solution)
        if mask in seen:
            continue
        seen.add(mask)
        yield mask, len(kernel_basis)
        if len(seen) >= maximum:
            return


def rational_is_square(value) -> bool:
    value = QQ(value)
    if value < 0:
        return False
    return ZZ(value.numerator()).is_square() and ZZ(value.denominator()).is_square()


def integral_square_scale(element):
    denominator = lcm([QQ(value).denominator() for value in element.list()] or [1])
    scaled = element * denominator**2
    if not scaled.is_integral():
        raise ValueError("square scaling did not produce an algebraic integer")
    return scaled, ZZ(denominator)


def search(
    base_quotient,
    maximum_candidates: int,
    auxiliary_primes: list[int],
    witness_prime_count: int,
    seed: int,
):
    field = NumberField(base_quotient.change_ring(QQ), "a")
    rational_primes = [2, *auxiliary_primes]
    s_unit_group = field.S_unit_group(
        proof=False, S=prod(rational_primes)
    )
    generators = list(s_unit_group.gens_values())
    embeddings = field.embeddings(RealField(160))
    columns = []
    for unit in generators:
        sign_mask = sum(
            (1 << index)
            for index, embedding in enumerate(embeddings)
            if embedding(unit) < 0
        )
        columns.append(state_bits(sign_mask, unit.norm(), rational_primes))

    row_count = 12 + len(rational_primes)
    matrix = Matrix(
        GF(2),
        row_count,
        len(generators),
        lambda row, column: columns[column][row],
    )
    target = vector(GF(2), [0] * 12 + [1] + [0] * len(auxiliary_primes))
    profiles, identities = maximal_joint_profiles()
    ring = PolynomialRing(ZZ, "x")
    x = ring.gen()
    results = []

    for solution_mask, kernel_dimension in affine_solutions(
        matrix, target, maximum_candidates, seed
    ):
        element = field.one()
        selected = []
        for index, unit in enumerate(generators):
            if (solution_mask >> index) & 1:
                element *= unit
                selected.append(index)
        scaled, square_scale = integral_square_scale(element)
        norm = QQ(scaled.norm())
        row = {
            "affineKernelDimension": kernel_dimension,
            "norm": str(norm),
            "normOver2IsSquare": rational_is_square(norm / 2),
            "selectedSUnitGeneratorIndexes": selected,
            "squareScale": str(square_scale),
            "totallyPositive": bool(scaled.is_totally_positive()),
        }
        if not row["normOver2IsSquare"] or not row["totallyPositive"]:
            row["status"] = "failed_exact_norm_or_sign_check"
            results.append(row)
            continue

        minimal = scaled.minpoly().change_ring(QQ)
        if minimal.degree() != 12 or any(QQ(value).denominator() != 1 for value in minimal):
            row["status"] = "nonintegral_or_nonprimitive_minpoly"
            results.append(row)
            continue
        minimal_zz = PolynomialRing(ZZ, "z")([ZZ(value) for value in minimal.list()])
        candidate = ring(minimal_zz(x**2))
        row.update(
            {
                "candidateCoefficientLine": coefficient_line(candidate),
                "candidateSha256": hashlib.sha256(
                    coefficient_line(candidate).encode("utf-8")
                ).hexdigest(),
                "irreducible": bool(candidate.is_irreducible()),
                "minimalPolynomial": str(minimal_zz),
                "realRoots": int(candidate.number_of_real_roots()),
            }
        )
        if not row["irreducible"] or row["realRoots"] != 24:
            row["status"] = "failed_degree24_checks"
            results.append(row)
            continue

        row["maximalSubgroupCertificate"] = frobenius_maximal_certificate(
            candidate,
            base_quotient,
            profiles,
            identities,
            witness_prime_count,
        )
        row["status"] = (
            "certified_24T20778_r24"
            if row["maximalSubgroupCertificate"]["complete"]
            else "contained_candidate_incomplete_maximal_certificate"
        )
        results.append(row)
        if row["status"] == "certified_24T20778_r24":
            break

    return {
        "auxiliaryPrimes": auxiliary_primes,
        "candidateResults": results,
        "sUnitGeneratorCount": len(generators),
        "sUnitPrimeIdealCount": len(s_unit_group.primes()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--aux-primes", default="37,41,53")
    parser.add_argument("--max-candidates", type=int, default=0)
    parser.add_argument("--witness-primes", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20778)
    args = parser.parse_args()
    if args.max_candidates < 0:
        raise ValueError("--max-candidates must be nonnegative")
    auxiliary_primes = [
        int(value) for value in args.aux_primes.split(",") if value.strip()
    ]
    if 2 in auxiliary_primes or len(set(auxiliary_primes)) != len(auxiliary_primes):
        raise ValueError("auxiliary primes must be distinct and must not include 2")

    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        rows = {
            str(key): load_even_ledger_polynomial(connection, key) for key in EXPECTED
        }
    finally:
        connection.close()

    base = rows[str(BASE_ROW)]["quotient"]
    base_field = NumberField(base.change_ring(QQ), "a")
    if not base.is_irreducible() or base.number_of_real_roots() != 12:
        raise ValueError("saved base is not irreducible and totally real")
    if abs(ZZ(base_field.discriminant())) != EXPECTED_BASE_DISC:
        raise ValueError("saved base-field discriminant changed")

    isomorphism_checks = {}
    for key in (CORE_229_ROW, CORE_687_ROW, CORE_3_ROW):
        other = rows[str(key)]["quotient"]
        other_field = NumberField(other.change_ring(QQ), "b")
        isomorphism_checks[rows[str(key)]["label"]] = bool(
            base_field.is_isomorphic(other_field)
        )
    if not all(isomorphism_checks.values()):
        raise ValueError("calibrating character fields are not isomorphic to the base")

    audit = {
        "baseField": {
            "definingPolynomial": str(base),
            "discriminant": str(abs(ZZ(base_field.discriminant()))),
            "signature": [int(value) for value in base_field.signature()],
        },
        "calibratingFieldIsomorphisms": isomorphism_checks,
        "characterAlignment": character_alignment_certificate(base),
        "ledgerRows": {
            name: {
                key: value
                for key, value in row.items()
                if key != "quotient"
            }
            for name, row in rows.items()
        },
        "networkCalls": 0,
        "submissionCalls": 0,
        "targetStructure": target_structure_certificate(),
    }

    result = {"audit": audit, "search": None}
    if args.max_candidates:
        result["search"] = search(
            base,
            args.max_candidates,
            auxiliary_primes,
            args.witness_primes,
            args.seed,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["search"] is None:
        return 0
    return 0 if any(
        row["status"] == "certified_24T20778_r24"
        for row in result["search"]["candidateResults"]
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
