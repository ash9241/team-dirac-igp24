#!/usr/bin/env sage -python
"""Exact cubic-resolvent Selmer gate for 24T24429.

This is a deterministic arithmetic gate, not a coefficient search.  For each
owned 6T10 quotient field and prescribed discriminant squareclass it computes

    K = M(sqrt(delta), zeta_3),   K0 = E(sqrt(delta), zeta_3),

the finite K(S,3) space for the natural bad-prime support, the two
anti-invariant conditions (cyclotomic and discriminant involutions), and the
norm/corestriction-zero condition K -> K0.

Sage's stock Selmer implementation can create a catastrophically large
principal ideal when a class-group generator has order divisible by 3.  The
routine below uses the mathematically equivalent reduced representative of
the order-three class before principalizing its cube.  Coordinates are
certified independently by valuations and exact cubic residue characters.

No network, staging, submission, or coefficient-box operation occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from sage.all import (
    GF,
    Matrix,
    NumberField,
    PolynomialRing,
    QQ,
    ZZ,
    identity_matrix,
    pari,
    prime_range,
    prod,
    proof,
    vector,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "data" / "gate_24429_cubic_selmer_20260730.json"
P = ZZ(3)
F3 = GF(3)


CASES = {
    "m5_positive": {
        "quadraticPolynomial": [44, -14, 1],
        "delta": "e/2",
        "relativeCubic": "m^3-(3/2)*e*m-e",
        "relativeCubicKind": "m5",
        "rationalSupport": [2, 3, 5, 11],
        "quotientPolynomial": [44, 132, 99, -14, -21, 0, 1],
        "quotientRealRoots": 6,
    },
    "m5_mixed": {
        "quadraticPolynomial": [44, -14, 1],
        "delta": "(e-6)/2",
        "relativeCubic": "m^3-(3/2)*e*m-e",
        "relativeCubicKind": "m5",
        "rationalSupport": [2, 3, 5, 11],
        "quotientPolynomial": [44, 132, 99, -14, -21, 0, 1],
        "quotientRealRoots": 6,
    },
    "m13_mixed": {
        "quadraticPolynomial": [17, -9, 1],
        "delta": "e-4",
        "relativeCubic": "m^3-3*m-e",
        "relativeCubicKind": "m13",
        "rationalSupport": [2, 3, 13],
        "quotientPolynomial": [17, 27, 9, -9, -6, 0, 1],
        "quotientRealRoots": 2,
    },
}


def atomic_json(path: Path, payload: object) -> str:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return hashlib.sha256(rendered.encode()).hexdigest()


def matrix_rows(matrix) -> list[list[int]]:
    return [[int(entry) for entry in row] for row in matrix.rows()]


def rank_mod_prime(rows, prime=3) -> int:
    """Small pure-Python row rank, avoiding a Sage 10.9 SIGILL edge case."""

    work = [
        [int(value) % prime for value in row]
        for row in rows
        if any(int(value) % prime for value in row)
    ]
    if not work:
        return 0
    columns = len(work[0])
    pivot_row = 0
    for column in range(columns):
        pivot = next(
            (
                index
                for index in range(pivot_row, len(work))
                if work[index][column] % prime
            ),
            None,
        )
        if pivot is None:
            continue
        work[pivot_row], work[pivot] = work[pivot], work[pivot_row]
        inverse = pow(work[pivot_row][column], -1, prime)
        work[pivot_row] = [
            (inverse * value) % prime for value in work[pivot_row]
        ]
        for index in range(len(work)):
            if index == pivot_row:
                continue
            multiple = work[index][column] % prime
            if not multiple:
                continue
            work[index] = [
                (left - multiple * right) % prime
                for left, right in zip(work[index], work[pivot_row])
            ]
        pivot_row += 1
        if pivot_row == len(work):
            break
    return pivot_row


def matrix_product_mod_prime(left, right, prime=3):
    left_rows = matrix_rows(left) if hasattr(left, "rows") else left
    right_rows = matrix_rows(right) if hasattr(right, "rows") else right
    if not left_rows or not right_rows:
        return []
    inner = len(right_rows)
    columns = len(right_rows[0])
    if len(left_rows[0]) != inner:
        raise ValueError("incompatible matrix dimensions")
    return [
        [
            sum(left_rows[i][k] * right_rows[k][j] for k in range(inner))
            % prime
            for j in range(columns)
        ]
        for i in range(len(left_rows))
    ]


def identity_rows(dimension):
    return [
        [int(row == column) for column in range(dimension)]
        for row in range(dimension)
    ]


def element_payload(value) -> dict:
    return {
        "coefficients": [str(coefficient) for coefficient in value.list()],
        "element": str(value),
    }


def prime_payload(prime_ideal, class_group=None) -> dict:
    row = {
        "absoluteNorm": str(prime_ideal.absolute_norm()),
        "ideal": str(prime_ideal),
        "ramificationIndex": int(prime_ideal.ramification_index()),
        "residueDegree": int(prime_ideal.residue_class_degree()),
        "smallestInteger": int(prime_ideal.smallest_integer()),
    }
    if class_group is not None:
        row["classCoordinates"] = [
            int(value) for value in class_group(prime_ideal).exponents()
        ]
    return row


def build_tower(name: str):
    specification = CASES[name]
    rational_ring = PolynomialRing(QQ, "x")
    x = rational_ring.gen()
    e_polynomial = rational_ring(specification["quadraticPolynomial"])
    E = NumberField(e_polynomial, "e")
    e = E.gen()
    if name == "m5_positive":
        delta = e / 2
    elif name == "m5_mixed":
        delta = (e - 6) / 2
    elif name == "m13_mixed":
        delta = e - 4
    else:
        raise ValueError(f"unknown case {name}")

    square_ring = PolynomialRing(E, "s")
    square_variable = square_ring.gen()
    B = E.extension(square_variable**2 - delta, "s")
    s = B.gen()

    cyclotomic_ring = PolynomialRing(B, "z")
    cyclotomic_variable = cyclotomic_ring.gen()
    K0 = B.extension(
        cyclotomic_variable**2 + cyclotomic_variable + 1,
        "z",
    )
    z = K0.gen()
    e_in_K0 = K0(B(E(e)))

    cubic_ring = PolynomialRing(K0, "m")
    cubic_variable = cubic_ring.gen()
    if specification["relativeCubicKind"] == "m5":
        cubic_polynomial = (
            cubic_variable**3
            - K0(3) / 2 * e_in_K0 * cubic_variable
            - e_in_K0
        )
    else:
        cubic_polynomial = cubic_variable**3 - K0(3) * cubic_variable - e_in_K0
    K = K0.extension(cubic_polynomial, "m")
    m = K.gen()

    # Build the two commuting involutions on K.  The discriminant
    # involution changes sqrt(delta), while cyclotomic conjugation changes
    # zeta_3.  Both fix the chosen root m of M/E.
    e_in_K = K(K0(B(E(e))))
    E_to_K = E.hom([e_in_K], K)
    B_identity_to_K = B.hom([K(s)], K, base_map=E_to_K)
    B_discriminant_to_K = B.hom([-K(s)], K, base_map=E_to_K)
    K0_discriminant_to_K = K0.hom(
        [K(z)],
        K,
        base_map=B_discriminant_to_K,
    )
    K0_cyclotomic_to_K = K0.hom(
        [-1 - K(z)],
        K,
        base_map=B_identity_to_K,
    )
    discriminant_involution_K = K.hom(
        [m],
        K,
        base_map=K0_discriminant_to_K,
    )
    cyclotomic_involution_K = K.hom(
        [m],
        K,
        base_map=K0_cyclotomic_to_K,
    )

    A = K.absolute_field(f"a_{name}")
    A_to_K, K_to_A = A.structure()
    A0 = K0.absolute_field(f"b_{name}")
    A0_to_K0, K0_to_A0 = A0.structure()
    discriminant_involution_A = A.hom(
        [
            K_to_A(
                discriminant_involution_K(A_to_K(A.gen()))
            )
        ],
        A,
    )
    cyclotomic_involution_A = A.hom(
        [K_to_A(cyclotomic_involution_K(A_to_K(A.gen())))],
        A,
    )

    return {
        "A": A,
        "A0": A0,
        "A0_to_K0": A0_to_K0,
        "A_to_K": A_to_K,
        "B": B,
        "E": E,
        "K": K,
        "K0": K0,
        "K0_to_A0": K0_to_A0,
        "K_to_A": K_to_A,
        "cyclotomic": cyclotomic_involution_A,
        "delta": delta,
        "discriminant": discriminant_involution_A,
        "e": e,
        "s": s,
        "specification": specification,
        "z": z,
    }


def class_element_from_coordinates(class_group, coordinates):
    element = class_group(class_group.number_field().ideal(1))
    for generator, exponent in zip(class_group.gens(), coordinates):
        element *= generator ** ZZ(exponent)
    return element


def principal_generator_mod_cubes(field, bnf, principal_ideal, p=P):
    """Principalize in PARI's compact factored form and reduce mod p powers."""

    class_log, factored = bnf.bnfisprincipal(principal_ideal.pari_hnf(), 4)
    class_log_values = [ZZ(value) for value in list(class_log)]
    if any(class_log_values):
        raise ArithmeticError("PARI reports a nonprincipal ideal")
    rows, columns = [int(value) for value in factored.matsize()]
    if columns != 2:
        raise ArithmeticError("unexpected PARI factored-generator shape")
    representative = field(1)
    factor_rows = []
    formal_ideal_valuations = {}
    for index in range(rows):
        factor = field(factored[index, 0])
        exponent = ZZ(factored[index, 1])
        residue_exponent = exponent % p
        removed_exponent = (exponent - residue_exponent) // p
        representative *= factor**residue_exponent
        for prime_ideal, valuation in field.ideal(factor).factor():
            formal_ideal_valuations[prime_ideal] = (
                formal_ideal_valuations.get(prime_ideal, ZZ(0))
                + exponent * ZZ(valuation)
            )
        factor_rows.append(
            {
                "factorCoefficients": [
                    str(coefficient) for coefficient in factor.list()
                ],
                "reducedExponent": int(residue_exponent),
                "removedCubeRootExponent": int(removed_exponent),
                "unreducedExponent": int(exponent),
            }
        )
    formal_ideal_valuations = {
        prime_ideal: exponent
        for prime_ideal, exponent in formal_ideal_valuations.items()
        if exponent
    }
    target_ideal_valuations = {
        prime_ideal: ZZ(exponent)
        for prime_ideal, exponent in principal_ideal.factor()
        if exponent
    }
    if formal_ideal_valuations != target_ideal_valuations:
        raise ArithmeticError("PARI factored generator failed exact ideal identity")
    ideal_ratio = field.ideal(representative) / principal_ideal
    ratio_factors = list(ideal_ratio.factor())
    if any(ZZ(exponent) % p for _, exponent in ratio_factors):
        raise ArithmeticError("factored principalization changed an ideal valuation mod p")
    return representative, {
        "classLog": [int(value) for value in class_log_values],
        "factoredIdealIdentityVerified": True,
        "factoredGenerator": factor_rows,
        "idealRatioValuationsDivisibleByP": True,
        "idealRatioFactorization": [
            {"exponent": int(exponent), "primeIdeal": str(prime_ideal)}
            for prime_ideal, exponent in ratio_factors
        ],
    }


def reduced_selmer_generators(field, prime_ideals, p=P):
    """Construct a complete candidate basis of K(S,p).

    Completeness is conditional only on the computed class group being an
    upper quotient of the true class group.  `bnfcertify(1)` below supplies
    that unconditional certificate when requested.  Independence is proved
    separately by exact local characters.
    """

    class_group = field.class_group(proof=False)
    bnf = field.pari_bnf(proof=False)
    class_orders = [ZZ(value) for value in class_group.gens_orders()]
    p_indices = [
        index for index, order in enumerate(class_orders) if order % p == 0
    ]

    if p_indices:
        class_mod_matrix = Matrix(
            GF(p),
            [
                [
                    ZZ(class_group(prime_ideal).exponents()[index]) % p
                    for index in p_indices
                ]
                for prime_ideal in prime_ideals
            ],
        )
    else:
        class_mod_matrix = Matrix(GF(p), len(prime_ideals), 0)
    support_kernel = class_mod_matrix.left_kernel()

    generators = []
    metadata = []

    # Ideals supported on S whose class is a p-th power.
    for basis_index, support_vector in enumerate(support_kernel.basis()):
        supported_ideal = prod(
            (
                prime_ideal ** ZZ(exponent)
                for prime_ideal, exponent in zip(
                    prime_ideals,
                    support_vector,
                )
            ),
            field.ideal(1),
        )
        class_coordinates = [
            ZZ(value) for value in class_group(supported_ideal).exponents()
        ]
        root_coordinates = []
        for coordinate, order in zip(class_coordinates, class_orders):
            if order % p == 0:
                if coordinate % p:
                    raise ArithmeticError(
                        "support-kernel class is not p-divisible"
                    )
                root_coordinates.append(coordinate // p)
            else:
                root_coordinates.append(
                    (coordinate * p.inverse_mod(order)) % order
                    if order > 1
                    else ZZ(0)
                )
        root_class = class_element_from_coordinates(
            class_group,
            root_coordinates,
        )
        root_ideal = root_class.ideal().reduce_equiv()
        if class_group(root_ideal) ** p != class_group(supported_ideal):
            raise ArithmeticError("reduced root ideal has the wrong class")
        principal_ideal = supported_ideal / root_ideal**p
        generator, principalization = principal_generator_mod_cubes(
            field,
            bnf,
            principal_ideal,
            p,
        )
        generators.append(generator)
        metadata.append(
            {
                "basisIndex": basis_index,
                "kind": "support",
                "principalization": principalization,
                "rootClassCoordinates": [
                    int(value) for value in root_coordinates
                ],
                "supportVector": [int(value) for value in support_vector],
            }
        )

    # Replace c^order by the cube of a reduced ideal representing the
    # order-p class c^(order/p).  This spans the same Cl[p] contribution
    # and avoids enormous unreduced principal ideals.
    for class_index in p_indices:
        order = class_orders[class_index]
        torsion_class = class_group.gen(class_index) ** (order // p)
        torsion_ideal = torsion_class.ideal().reduce_equiv()
        if class_group(torsion_ideal).order() != p:
            raise ArithmeticError("reduced class representative is not order p")
        principal_ideal = torsion_ideal**p
        generator, principalization = principal_generator_mod_cubes(
            field,
            bnf,
            principal_ideal,
            p,
        )
        generators.append(generator)
        metadata.append(
            {
                "classGeneratorIndex": class_index,
                "classGeneratorOrder": int(order),
                "kind": "class_p_torsion",
                "principalization": principalization,
                "reducedTorsionIdeal": str(torsion_ideal),
                "reducedTorsionIdealNorm": str(torsion_ideal.norm()),
            }
        )

    unit_group = field.unit_group(proof=False)
    unit_generators = list(unit_group.gens_values())
    if unit_group.zeta_order() % p:
        unit_generators = unit_generators[1:]
    for unit_index, generator in enumerate(unit_generators):
        generators.append(generator)
        metadata.append({"kind": "unit", "unitIndex": unit_index})

    expected_dimension = (
        int(support_kernel.dimension())
        + len(p_indices)
        + len(unit_generators)
    )
    if len(generators) != expected_dimension:
        raise ArithmeticError("Selmer generator count disagrees with upper bound")

    support_set = set(prime_ideals)
    validity = []
    for generator in generators:
        factors = list(field.ideal(generator).factor())
        valid = all(
            prime_ideal in support_set or ZZ(exponent) % p == 0
            for prime_ideal, exponent in factors
        )
        validity.append(valid)
    if not all(validity):
        raise ArithmeticError("Selmer generator has bad valuation outside S")

    return {
        "classGroup": class_group,
        "classModMatrix": class_mod_matrix,
        "expectedDimension": expected_dimension,
        "generators": generators,
        "metadata": metadata,
        "pClassIndices": p_indices,
        "supportKernel": support_kernel,
        "unitGeneratorCount": len(unit_generators),
    }


class ExactCharacterCoordinates:
    """Injective exact coordinates using valuations and cubic residues."""

    def __init__(
        self,
        field,
        support,
        generators,
        zeta3,
        target_elements,
        rational_support,
        maximum_auxiliary_prime=5000,
    ):
        self.field = field
        self.support = list(support)
        self.generators = list(generators)
        self.zeta3 = field(zeta3)
        self.rows = []
        self.row_metadata = []
        self.row_evaluators = []
        self.pari_nf = field.pari_nf()

        for prime_ideal in self.support:
            self.rows.append(
                [
                    int(generator.valuation(prime_ideal) % P)
                    for generator in self.generators
                ]
            )
            self.row_metadata.append(
                {
                    "kind": "valuation",
                    "primeIdeal": str(prime_ideal),
                    "absoluteNorm": str(prime_ideal.absolute_norm()),
                }
            )
            self.row_evaluators.append(("valuation", prime_ideal, None, None))

        selected_rank = Matrix(F3, self.rows).rank() if self.rows else 0
        all_values = [*self.generators, *target_elements]
        for rational_prime in prime_range(5, maximum_auxiliary_prime + 1):
            if rational_prime in rational_support:
                continue
            for prime_ideal in field.primes_above(rational_prime):
                if any(value.valuation(prime_ideal) != 0 for value in all_values):
                    continue
                residue_order = ZZ(prime_ideal.absolute_norm())
                if (residue_order - 1) % 3:
                    continue
                try:
                    modpr = self.pari_nf.nfmodprinit(prime_ideal.pari_prime())
                    residue_zeta = self.pari_nf.nfmodpr(
                        pari(self.zeta3),
                        modpr,
                    )
                except Exception:
                    continue
                if residue_zeta == 1 or residue_zeta**3 != 1:
                    continue
                try:
                    row = [
                        self._cubic_character(
                            self.pari_nf.nfmodpr(pari(generator), modpr),
                            residue_zeta,
                            residue_order,
                        )
                        for generator in self.generators
                    ]
                except Exception:
                    continue
                trial_rows = [*self.rows, row]
                trial_rank = Matrix(F3, trial_rows).rank()
                if trial_rank == selected_rank:
                    continue
                self.rows.append(row)
                self.row_metadata.append(
                    {
                        "absoluteNorm": str(prime_ideal.absolute_norm()),
                        "ideal": str(prime_ideal),
                        "kind": "cubic_residue",
                        "rationalPrime": int(rational_prime),
                    }
                )
                self.row_evaluators.append(
                    ("cubic_residue", prime_ideal, modpr, residue_zeta)
                )
                selected_rank = trial_rank
                if selected_rank == len(self.generators):
                    break
            if selected_rank == len(self.generators):
                break
        self.matrix = Matrix(F3, self.rows)
        if self.matrix.rank() != len(self.generators):
            raise ArithmeticError(
                "auxiliary cubic characters did not certify Selmer independence"
            )

    @staticmethod
    def _cubic_character(residue, residue_zeta, residue_order):
        if not residue:
            raise ValueError("cubic character is undefined at zero")
        value = residue ** ((residue_order - 1) // 3)
        if value == 1:
            return 0
        if value == residue_zeta:
            return 1
        if value == residue_zeta**2:
            return 2
        raise ArithmeticError("cubic residue did not land in mu_3")

    def signature(self, value):
        value = self.field(value)
        signature = []
        for metadata, evaluator in zip(
            self.row_metadata,
            self.row_evaluators,
        ):
            kind, prime_ideal, modpr, residue_zeta = evaluator
            if kind == "valuation":
                signature.append(int(value.valuation(prime_ideal) % P))
                continue
            if value.valuation(prime_ideal) != 0:
                raise ArithmeticError(
                    "target is not a unit at an auxiliary character prime"
                )
            signature.append(
                self._cubic_character(
                    self.pari_nf.nfmodpr(pari(value), modpr),
                    residue_zeta,
                    ZZ(prime_ideal.absolute_norm()),
                )
            )
        return vector(F3, signature)

    def coordinates(self, value):
        signature = self.signature(value)
        coordinates = self.matrix.solve_right(signature)
        if self.matrix * coordinates != signature:
            raise ArithmeticError("target does not lie in certified Selmer span")
        return coordinates

    def payload(self):
        return {
            "evaluationMatrix": matrix_rows(self.matrix),
            "evaluationRank": int(self.matrix.rank()),
            "rows": [
                {**metadata, "row": row}
                for metadata, row in zip(self.row_metadata, self.rows)
            ],
        }


def product_from_coordinates(field, generators, coordinates):
    return prod(
        (
            generator ** ZZ(exponent)
            for generator, exponent in zip(generators, coordinates)
        ),
        field(1),
    )


def coordinate_targets(coordinate_system, generators, targets):
    rows = []
    for target in targets:
        coordinates = coordinate_system.coordinates(target)
        reconstructed = product_from_coordinates(
            coordinate_system.field,
            generators,
            coordinates,
        )
        quotient = target / reconstructed
        cube_verified = bool(quotient.is_nth_power(3))
        if not cube_verified:
            raise ArithmeticError("coordinate quotient is not an exact cube")
        rows.append(
            {
                "coordinates": [int(value) for value in coordinates],
                "quotientCubeVerified": cube_verified,
            }
        )
    return rows


def certify_class_group_upper(field, enabled: bool) -> dict:
    class_group = field.class_group(proof=False)
    result = {
        "computedInvariants": [
            int(value) for value in class_group.invariants()
        ],
        "computedOrder": int(class_group.order()),
        "unconditionalUpperQuotientCertified": False,
    }
    if not enabled:
        result["status"] = "not_requested"
        return result
    started = time.monotonic()
    # Flag 1 certifies only that the true class group is a quotient of the
    # computed one, so fundamental units are unnecessary and substantially
    # slow down these degree-24 fields.
    flag = int(field.pari_bnf(proof=False, units=False).bnfcertify(1))
    result.update(
        {
            "bnfcertifyFlag": flag,
            "certificationSeconds": round(time.monotonic() - started, 3),
            "status": "certified" if flag == 1 else "failed",
            "unconditionalUpperQuotientCertified": flag == 1,
        }
    )
    if flag != 1:
        raise ArithmeticError("PARI failed the class-group upper certificate")
    return result


def run_case(name: str, certify: bool) -> dict:
    started = time.monotonic()
    tower = build_tower(name)
    A = tower["A"]
    A0 = tower["A0"]
    K = tower["K"]
    K0 = tower["K0"]
    rational_support = tower["specification"]["rationalSupport"]
    support = A.ideal(prod(rational_support)).support()
    support0 = A0.ideal(prod(rational_support)).support()
    print(json.dumps({"case": name, "event": "tower_built"}), flush=True)

    selmer = reduced_selmer_generators(A, support)
    selmer0 = reduced_selmer_generators(A0, support0)
    generators = selmer["generators"]
    generators0 = selmer0["generators"]
    print(
        json.dumps(
            {
                "case": name,
                "dimension": len(generators),
                "event": "selmer_generators_built",
                "fixedDimension": len(generators0),
            }
        ),
        flush=True,
    )

    cyclotomic_targets = [
        tower["cyclotomic"](generator) for generator in generators
    ]
    discriminant_targets = [
        tower["discriminant"](generator) for generator in generators
    ]
    norm_targets0 = [
        tower["K0_to_A0"](
            tower["A_to_K"](generator).relative_norm()
        )
        for generator in generators
    ]
    restriction_targets = [
        tower["K_to_A"](
            K(tower["A0_to_K0"](generator))
        )
        for generator in generators0
    ]
    print(json.dumps({"case": name, "event": "map_targets_built"}), flush=True)

    coordinates = ExactCharacterCoordinates(
        A,
        support,
        generators,
        tower["K_to_A"](K(tower["z"])),
        [*cyclotomic_targets, *discriminant_targets, *restriction_targets],
        rational_support,
    )
    coordinates0 = ExactCharacterCoordinates(
        A0,
        support0,
        generators0,
        tower["K0_to_A0"](tower["z"]),
        norm_targets0,
        rational_support,
    )
    print(
        json.dumps({"case": name, "event": "coordinate_systems_built"}),
        flush=True,
    )

    cyclotomic_rows = coordinate_targets(
        coordinates,
        generators,
        cyclotomic_targets,
    )
    discriminant_rows = coordinate_targets(
        coordinates,
        generators,
        discriminant_targets,
    )
    norm_rows = coordinate_targets(
        coordinates0,
        generators0,
        norm_targets0,
    )
    restriction_rows = coordinate_targets(
        coordinates,
        generators,
        restriction_targets,
    )
    print(json.dumps({"case": name, "event": "map_coordinates_built"}), flush=True)

    cyclotomic_matrix = Matrix(
        F3,
        [row["coordinates"] for row in cyclotomic_rows],
    ).transpose()
    discriminant_matrix = Matrix(
        F3,
        [row["coordinates"] for row in discriminant_rows],
    ).transpose()
    norm_matrix = Matrix(
        F3,
        [row["coordinates"] for row in norm_rows],
    ).transpose()
    restriction_matrix = Matrix(
        F3,
        [row["coordinates"] for row in restriction_rows],
    ).transpose()
    print(json.dumps({"case": name, "event": "map_matrices_built"}), flush=True)

    dimension = len(generators)
    relation_matrix = (
        cyclotomic_matrix + identity_matrix(F3, dimension)
    ).stack(
        discriminant_matrix + identity_matrix(F3, dimension)
    ).stack(norm_matrix)
    admissible = relation_matrix.right_kernel()
    print(
        json.dumps(
            {
                "admissibleDimension": int(admissible.dimension()),
                "case": name,
                "event": "admissible_kernel_built",
            }
        ),
        flush=True,
    )
    restriction_rank = int(restriction_matrix.rank())
    restriction_column_rows = [
        [int(value) for value in column]
        for column in restriction_matrix.columns()
    ]
    admissible_rows = [
        [int(value) for value in basis] for basis in admissible.basis()
    ]
    union_rank = rank_mod_prime(
        [*admissible_rows, *restriction_column_rows]
    )
    restriction_intersection_dimension = (
        int(admissible.dimension()) + restriction_rank - union_rank
    )
    nonconstant_dimension = (
        int(admissible.dimension()) - restriction_intersection_dimension
    )
    print(
        json.dumps(
            {
                "case": name,
                "event": "restriction_quotient_built",
                "nonconstantDimension": nonconstant_dimension,
            }
        ),
        flush=True,
    )

    quotient_basis = []
    span_rows = list(restriction_column_rows)
    span_rank = restriction_rank
    for candidate in admissible.basis():
        candidate_row = [int(value) for value in candidate]
        trial_rank = rank_mod_prime([*span_rows, candidate_row])
        if trial_rank == span_rank:
            continue
        quotient_basis.append(candidate)
        span_rows.append(candidate_row)
        span_rank = trial_rank
    print(
        json.dumps(
            {
                "case": name,
                "event": "quotient_basis_built",
                "quotientBasisCount": len(quotient_basis),
            }
        ),
        flush=True,
    )

    class_certificate = certify_class_group_upper(A, certify)
    class_certificate0 = certify_class_group_upper(A0, certify)
    exact = bool(
        class_certificate["unconditionalUpperQuotientCertified"]
        and class_certificate0["unconditionalUpperQuotientCertified"]
    )
    print(
        json.dumps({"case": name, "event": "class_certificates_built"}),
        flush=True,
    )

    field_payload = {
        "absoluteDiscriminant": str(A.discriminant()),
        "absolutePolynomial": str(A.polynomial()),
        "degree": int(A.degree()),
        "signature": [int(value) for value in A.signature()],
    }
    fixed_field_payload = {
        "absoluteDiscriminant": str(A0.discriminant()),
        "absolutePolynomial": str(A0.polynomial()),
        "degree": int(A0.degree()),
        "signature": [int(value) for value in A0.signature()],
    }
    print(json.dumps({"case": name, "event": "field_payloads_built"}), flush=True)
    generator_payloads = [
        element_payload(generator) for generator in generators
    ]
    generator0_payloads = [
        element_payload(generator) for generator in generators0
    ]
    print(
        json.dumps({"case": name, "event": "generator_payloads_built"}),
        flush=True,
    )
    support_payloads = [
        prime_payload(prime_ideal, selmer["classGroup"])
        for prime_ideal in support
    ]
    support0_payloads = [
        prime_payload(prime_ideal, selmer0["classGroup"])
        for prime_ideal in support0
    ]
    print(
        json.dumps({"case": name, "event": "support_payloads_built"}),
        flush=True,
    )
    coordinate_payload = coordinates.payload()
    coordinate0_payload = coordinates0.payload()
    print(
        json.dumps({"case": name, "event": "coordinate_payloads_built"}),
        flush=True,
    )

    payload = {
        "case": name,
        "classGroupCertificate": class_certificate,
        "corestriction": {
            "matrix": matrix_rows(norm_matrix),
            "rank": int(norm_matrix.rank()),
        },
        "exactUnconditional": exact,
        "field": field_payload,
        "fixedField": fixed_field_payload,
        "involutions": {
            "commute": bool(
                matrix_product_mod_prime(
                    cyclotomic_matrix,
                    discriminant_matrix,
                )
                == matrix_product_mod_prime(
                    discriminant_matrix,
                    cyclotomic_matrix,
                )
            ),
            "cyclotomicMatrix": matrix_rows(cyclotomic_matrix),
            "cyclotomicSquareIsIdentity": bool(
                matrix_product_mod_prime(
                    cyclotomic_matrix,
                    cyclotomic_matrix,
                )
                == identity_rows(dimension)
            ),
            "discriminantMatrix": matrix_rows(discriminant_matrix),
            "discriminantSquareIsIdentity": bool(
                matrix_product_mod_prime(
                    discriminant_matrix,
                    discriminant_matrix,
                )
                == identity_rows(dimension)
            ),
        },
        "networkCalls": 0,
        "quotientOrbitGate": {
            "admissibleBasis": [
                [int(value) for value in basis]
                for basis in admissible.basis()
            ],
            "admissibleDimension": int(admissible.dimension()),
            "annihilatorMatrix": matrix_rows(relation_matrix),
            "nonconstantQuotientBasis": [
                [int(value) for value in basis]
                for basis in quotient_basis
            ],
            "nonconstantQuotientDimension": nonconstant_dimension,
            "restrictionIntersectionDimension": (
                restriction_intersection_dimension
            ),
            "restrictionMatrix": matrix_rows(restriction_matrix),
            "restrictionRank": restriction_rank,
            "survivesRankTwoLinearGate": nonconstant_dimension >= 1,
        },
        "runtimeSeconds": round(time.monotonic() - started, 3),
        "selmer": {
            "classMod3Matrix": matrix_rows(selmer["classModMatrix"]),
            "coordinateCertificate": coordinate_payload,
            "dimension": dimension,
            "generatorMetadata": selmer["metadata"],
            "generators": generator_payloads,
            "support": support_payloads,
            "supportKernelBasis": [
                [int(value) for value in basis]
                for basis in selmer["supportKernel"].basis()
            ],
        },
        "selmerFixedField": {
            "classMod3Matrix": matrix_rows(selmer0["classModMatrix"]),
            "classGroupCertificate": class_certificate0,
            "coordinateCertificate": coordinate0_payload,
            "dimension": len(generators0),
            "generatorMetadata": selmer0["metadata"],
            "generators": generator0_payloads,
            "support": support0_payloads,
            "supportKernelBasis": [
                [int(value) for value in basis]
                for basis in selmer0["supportKernel"].basis()
            ],
        },
        "specification": tower["specification"],
        "submissionCalls": 0,
    }
    print(json.dumps({"case": name, "event": "payload_built"}), flush=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        action="append",
        choices=sorted(CASES),
        default=[],
    )
    parser.add_argument("--certify", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    proof.number_field(False)
    pari.allocatemem(256 * 1024 * 1024, 4 * 1024 * 1024 * 1024)
    selected = args.case or list(CASES)
    rows = []
    for name in selected:
        print(
            json.dumps({"case": name, "event": "case_started"}, sort_keys=True),
            flush=True,
        )
        row = run_case(name, args.certify)
        rows.append(row)
        print(
            json.dumps(
                {
                    "admissibleDimension": row["quotientOrbitGate"][
                        "admissibleDimension"
                    ],
                    "case": name,
                    "event": "case_finished",
                    "exactUnconditional": row["exactUnconditional"],
                    "nonconstantQuotientDimension": row[
                        "quotientOrbitGate"
                    ]["nonconstantQuotientDimension"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    payload = {
        "cases": rows,
        "finiteSupportScope": (
            "all primes above the rational primes ramified in the owned "
            "quotient/discriminant/cyclotomic tower; this is the natural "
            "smallest bad-prime gate, not an exhaustion over new tame conductors"
        ),
        "networkCalls": 0,
        "schemaVersion": "24T24429-cubic-selmer-gate-v1",
        "submissionCalls": 0,
    }
    digest = atomic_json(args.output, payload)
    print(
        json.dumps(
            {
                "cases": selected,
                "output": str(args.output.resolve()),
                "sha256": digest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
