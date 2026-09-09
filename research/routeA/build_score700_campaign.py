#!/usr/bin/env python3
"""Build exact nontrivial-character banks for the score-700 push.

Two calibrated ``12T210`` quotients are used.

The crossed quotient

    q(x) = x^3 - 12x + 20,
    f(x) = (q(x)^2 - 468)^2 - 198288

has eight real roots and exact GAP Galois type ``12T210``.  Its crossed
quadratic relation supplies compact negative-norm generators for
``24T22548`` and ``24T22544``.  Negative norm forces the accessible root
counts to be 2 modulo 4, precisely the signatures missed by the earlier
totally-real campaign.

The second quotient is the already calibrated totally-real ``12T210``
field from the Funny Sock campaign.  An exact norm equation in its outer
biquadratic field supplies squareclass 1585, whose character kernel is
``24T22547``.

Every emitted row is passed through the unit-character maximal-subgroup
certificate before it becomes submission-ready.  The manifest is appended
incrementally, so an interrupted certification run can be resumed safely.

The second structured quotient is

    q(x) = x^3 - 12x + 15,
    f(x) = (q(x)^2 - 481)^2 - 108160.

It has eight real roots and exact Galois type ``12T214``.  Norm equations in
its outer biquadratic field, together with the crossed cubic-discriminant
character, realize all five non-isomorphic nontrivial character kernels
``24T22560`` through ``24T22564``.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

from sympy import Poly, Rational, resultant, symbols

from routeA.build_funny_sock_campaign import PROJECT, known_owned_pairs
from routeA.build_t00035_campaign import (
    _asc,
    _squarefree_part,
    certify_campaign,
    forecast,
    write_payload,
)
from routeA.constructions.unit_character_lift import (
    UnitCharacterFamily,
    family_from_polynomials,
)
from routeA.constructions.integral_basis_character_lift import (
    IntegralBasisCharacterFamily,
    build_integral_basis_character_bank,
    family_from_integral_basis_seed,
)
from routeA.ledger import DEFAULT_DB


_X, _T = symbols("x t")
DEFAULT_MANIFEST = PROJECT / "routeA" / "data" / "score700_campaign.jsonl"
DEFAULT_PAYLOAD = PROJECT / "routeA" / "score700_campaign.txt"


def _family(
    *,
    name: str,
    target_t: int,
    norm: int,
    base: Poly,
    generator: Any,
    roots: tuple[int, ...],
    base_roots: int,
    character: str,
) -> UnitCharacterFamily:
    generator_poly = Poly(generator, _X).rem(base)
    actual_norm = _squarefree_part(
        int(resultant(base.as_expr(), generator_poly.as_expr(), _X))
    )
    if actual_norm != int(norm):
        raise AssertionError(
            f"{name} norm squareclass is {actual_norm}, expected {norm}"
        )
    return family_from_polynomials(
        family=name,
        base_t=210,
        target_t=target_t,
        expected_norm_squareclass=norm,
        base_coefficients=_asc(base),
        generator_coefficients=_asc(generator_poly, base),
        target_root_counts=roots,
        construction_overgroup="C2^11_character_kernel_over_12T210",
        character_name=character,
        base_root_count=base_roots,
    )


def crossed_12t210_families() -> list[UnitCharacterFamily]:
    """Return the negative-character families on the eight-real-root base."""

    x, t = _X, _T
    q = x**3 - 12 * x + 20
    base = Poly((q**2 - 468) ** 2 - 198288, x)

    # Norm -34 in Q[t]/((t^2-468)^2-198288).  Scaling by 2592 changes
    # its quartic norm by a square and keeps the squareclass unchanged.
    negative_outer = Poly(
        (t**3 + 12 * t**2 - 792 * t + 864).subs(t, q), x
    ).rem(base).as_expr()

    # Norm +2 in the same outer field.  Its product with the crossed
    # character distinguishes 24T22548 from 24T22546 by the exact GAP map.
    outer_pair = Poly((t**3 + 36 * t**2 + 72 * t).subs(t, q), x).rem(base).as_expr()

    # q(-4)=4 is the lower critical value's simple companion root.  Together
    # with the negative outer norm it realizes the rational crossed character.
    companion = x + 4
    roots = (2, 6, 10, 14)
    return [
        _family(
            name="score700_crossed_12t210_22548",
            target_t=22548,
            norm=-1598,
            base=base,
            generator=negative_outer * companion * outer_pair,
            roots=roots,
            base_roots=8,
            character="crossed_fiber_times_outer_pair",
        ),
        _family(
            name="score700_crossed_12t210_22544",
            target_t=22544,
            norm=-799,
            base=base,
            generator=negative_outer * companion,
            roots=roots,
            base_roots=8,
            character="crossed_fiber",
        ),
    ]


def outer_d_12t210_family() -> UnitCharacterFamily:
    """Return the totally-real 24T22547 outer-d character family."""

    x, t = _X, _T
    q = x**3 + 9 * x**2 - 74
    base = Poly((q**2 - 181) ** 2 - 14265, x)

    # This integral multiple of an exact norm-1585 element in the outer
    # biquadratic field was found with PARI bnfisintnorm.
    # Keep the large products explicit and typo-resistant.
    outer_norm = (
        294959682337 * t**3
        - (11344759286 * 204) * t**2
        - (44308279434629 * 2) * t
        + (3408376091297 * 204)
    )
    generator = Poly(outer_norm.subs(t, q), x).rem(base).as_expr()
    return _family(
        name="score700_outer_d_12t210_22547",
        target_t=22547,
        norm=1585,
        base=base,
        generator=generator,
        roots=(0, 4, 8, 12, 16, 20, 24),
        base_roots=12,
        character="outer_d",
    )


def structured_12t214_families() -> list[UnitCharacterFamily]:
    """Return one calibrated family for every nontrivial 12T214 target."""

    x, t = _X, _T
    q = x**3 - 12 * x + 15
    base = Poly((q**2 - 481) ** 2 - 108160, x)

    # Exact outer-field norm generators.  Their norm squareclasses are
    # respectively 10, 26, and 65.  The first and third characters have
    # conjugate degree-24 permutation representations, so the lower-height
    # norm-10 family is sufficient for 24T22560.
    outer_10 = 442260 - 35035 * t - 648 * t**2 + 47 * t**3
    outer_26 = -11583 + 1222 * t + 27 * t**2 - 2 * t**3
    outer_65 = -9945 + 845 * t + 9 * t**2 - t**3

    # This outer element has norm -1.  Since q(4)=31 is a cubic critical
    # value and core(p(31))=1910, multiplying its pullback by x-4 realizes
    # the crossed squareclass -1910.  Products with the three outer
    # characters give the other crossed characters.
    negative_outer = -18603 + 1313 * t + 27 * t**2 - t**3
    crossed = negative_outer.subs(t, q) * (x - 4)

    positive_roots = (0, 4, 8, 12, 16)
    negative_roots = (2, 6, 10, 14)
    return [
        _family(
            name="score700_structured_12t214_22560",
            target_t=22560,
            norm=10,
            base=base,
            generator=outer_10.subs(t, q),
            roots=positive_roots,
            base_roots=8,
            character="outer_10",
        ),
        _family(
            name="score700_structured_12t214_22562",
            target_t=22562,
            norm=26,
            base=base,
            generator=outer_26.subs(t, q),
            roots=positive_roots,
            base_roots=8,
            character="outer_26",
        ),
        _family(
            name="score700_structured_12t214_22563",
            target_t=22563,
            norm=-1910,
            base=base,
            generator=crossed,
            roots=negative_roots,
            base_roots=8,
            character="crossed_fiber",
        ),
        _family(
            name="score700_structured_12t214_22564",
            target_t=22564,
            norm=-191,
            base=base,
            generator=crossed * outer_10.subs(t, q),
            roots=negative_roots,
            base_roots=8,
            character="crossed_fiber_times_outer_10",
        ),
        _family(
            name="score700_structured_12t214_22561",
            target_t=22561,
            norm=-4966,
            base=base,
            generator=crossed * outer_65.subs(t, q),
            roots=negative_roots,
            base_roots=8,
            character="crossed_fiber_times_outer_65",
        ),
    ]


_BASE_12T156_INTEGRAL = (
    13520, -40560, -30420, 94900, 71565, -21060, -19381,
    1674, 1926, -46, -78, 0, 1,
)

_NORM_605_SEED = (
    "-7657403003885/41826499577",
    "42180280659303/334611996616",
    "437217933910199/669223993232",
    "5295848071876773/17399823824032",
    "-1332739749153159/8699911912016",
    "-1622131649236473/17399823824032",
    "110971888510643/8699911912016",
    "82531015405725/8699911912016",
    "-3409000996833/8699911912016",
    "-3345954311243/8699911912016",
    "17464573937/8699911912016",
    "85267775449/17399823824032",
)


def catalog_12t156_sign_family() -> UnitCharacterFamily:
    """Return the low-discriminant permutation-sign character of 12T156."""

    x = _X
    coefficients = (
        784, 4704, 10584, 9856, 693, -4914, -2237,
        660, 495, -26, -39, 0, 1,
    )
    base = Poly(sum(value * x**i for i, value in enumerate(coefficients)), x)
    return _family(
        name="score700_catalog_12t156_sign_21376",
        target_t=21376,
        norm=29,
        base=base,
        generator=base.diff().as_expr(),
        roots=(0, 4, 8, 12, 16, 20, 24),
        base_roots=12,
        character="permutation_sign",
    )


def integral_basis_12t156_families() -> list[IntegralBasisCharacterFamily]:
    """Return the norm-5 outer character and its permutation-sign product."""

    x = _X
    base = Poly(
        sum(value * x**i for i, value in enumerate(_BASE_12T156_INTEGRAL)),
        x,
        domain="ZZ",
    )
    seed = Poly(
        sum(
            Rational(Fraction(value).numerator, Fraction(value).denominator) * x**i
            for i, value in enumerate(_NORM_605_SEED)
        ),
        x,
        domain="QQ",
    )
    product = Poly(seed.as_expr() * base.diff().as_expr(), x, domain="QQ").rem(base)
    product_seed = tuple(
        Fraction(int(product.nth(i).p), int(product.nth(i).q))
        for i in range(12)
    )
    common = {
        "base_t": 156,
        "base_coefficients": _BASE_12T156_INTEGRAL,
        "target_root_counts": tuple(range(0, 25, 4)),
        "construction_overgroup": "C2^11_character_kernel_over_12T156",
        "base_root_count": 12,
    }
    return [
        family_from_integral_basis_seed(
            family="score700_integral_basis_12t156_21372",
            target_t=21372,
            expected_norm_squareclass=5,
            seed_coefficients=_NORM_605_SEED,
            character_name="d4_quadratic_5",
            **common,
        ),
        family_from_integral_basis_seed(
            family="score700_integral_basis_12t156_21374",
            target_t=21374,
            expected_norm_squareclass=505,
            seed_coefficients=product_seed,
            character_name="d4_quadratic_5_times_permutation_sign_101",
            **common,
        ),
    ]


def integral_basis_12t243_families() -> list[IntegralBasisCharacterFamily]:
    """Return a full-rank outer-sign lift on the catalog 12T243 field."""

    base = (
        3056, -9168, -6876, 25156, 13863, -17964, -11676,
        1488, 1467, -32, -66, 0, 1,
    )
    # The norm-19 subfield generator alone has a deficient Kummer orbit.
    # Multiplying it by principal prime ideals 1 and 4 above 61 preserves
    # the norm squareclass (19*61^2) and gives the full even permutation
    # module.  The resulting character is exactly 24T23290.
    seed = (
        "13839488390388835/117115029269124",
        "-9644593758484679/29278757317281",
        "-89441399510835359/234230058538248",
        "902290829838572383/936920234152992",
        "203486786658011339/234230058538248",
        "-685265216924211/1148186561462",
        "-39845410411541041/58557514634562",
        "-66450536808341797/936920234152992",
        "6814567008820697/117115029269124",
        "4754429963418857/468460117076496",
        "-77387600533099/58557514634562",
        "-81588240332397/312306744717664",
    )
    return [family_from_integral_basis_seed(
        family="score700_integral_basis_12t243_23290",
        base_t=243,
        target_t=23290,
        expected_norm_squareclass=19,
        base_coefficients=base,
        seed_coefficients=seed,
        target_root_counts=tuple(range(0, 25, 4)),
        construction_overgroup="C2^11_character_kernel_over_12T243",
        character_name="d4_outer_sign_19_with_generic_prime_pair",
        base_root_count=12,
    )]


_LINEAR_NORM_CHARACTERS = (
    # base_t, target_t, norm squareclass, shift c, catalog polynomial
    (52, 18475, 2, 0, (2, -24, 96, -108, -170, 380, -22, -252, 65, 56, -16, -4, 1)),
    (109, 19725, 2, 0, (2, 20, -14, -248, 374, 116, -354, 0, 128, -4, -20, 0, 1)),
    (136, 20754, 5, 1, (-1, 7, 2, -54, 4, 123, -12, -106, 16, 37, -8, -4, 1)),
    (173, 21589, 2, -1, (752, 4512, 10152, 9480, 783, -4536, -2068, 600, 450, -24, -36, 0, 1)),
    (209, 22540, 5, -1, (11, 132, 594, 1150, 549, -1026, -987, 234, 351, -12, -36, 0, 1)),
    (215, 22568, 5, -1, (1216, 7296, 16416, 15424, 1692, -6696, -3084, 792, 594, -28, -42, 0, 1)),
    (216, 22571, 5, 0, (5, -75, -360, 30, 795, 45, -644, -12, 216, -3, -27, 0, 1)),
    (262, 23803, 5, -1, (1136, 6816, 15336, 14384, 1467, -6426, -2949, 792, 594, -28, -42, 0, 1)),
)


def linear_norm_character_families() -> list[IntegralBasisCharacterFamily]:
    """Return catalog characters with an exact linear norm generator.

    For these fields, ``f(c) = d*s^2`` at the listed integer ``c``.  Hence
    ``alpha-c`` has rational norm squareclass ``d``.  Joint Frobenius/
    Kronecker distributions identify the exact character target; the usual
    maximal-subgroup proof certifies every emitted degree-24 field.
    """

    families: list[IntegralBasisCharacterFamily] = []
    for base_t, target_t, norm_class, shift, coefficients in _LINEAR_NORM_CHARACTERS:
        value = sum(coefficient * shift**power for power, coefficient in enumerate(coefficients))
        if _squarefree_part(value) != norm_class:
            raise AssertionError(
                f"12T{base_t} linear norm core is {_squarefree_part(value)}, "
                f"expected {norm_class}"
            )
        families.append(family_from_integral_basis_seed(
            family=f"score700_linear_norm_12t{base_t}_{target_t}",
            base_t=base_t,
            target_t=target_t,
            expected_norm_squareclass=norm_class,
            base_coefficients=coefficients,
            seed_coefficients=(-shift, 1),
            target_root_counts=tuple(range(0, 25, 4)),
            construction_overgroup=f"C2^11_linear_norm_character_over_12T{base_t}",
            character_name=f"linear_norm_squareclass_{norm_class}",
            base_root_count=12,
        ))
    return families


def linear_norm_sign_product_families() -> list[IntegralBasisCharacterFamily]:
    """Return the three valuable linear-character/permutation-sign products."""

    targets = {52: (18473, 158), 209: (22541, 61), 262: (23802, 205)}
    families: list[IntegralBasisCharacterFamily] = []
    for base_t, _, norm_class, shift, coefficients in _LINEAR_NORM_CHARACTERS:
        if base_t not in targets:
            continue
        target_t, product_class = targets[base_t]
        x = _X
        base = Poly(sum(value * x**i for i, value in enumerate(coefficients)), x)
        seed = Poly((x - shift) * base.diff().as_expr(), x).rem(base)
        seed_coefficients = tuple(int(seed.nth(i)) for i in range(12))
        actual = _squarefree_part(
            int(resultant(base.as_expr(), seed.as_expr(), x))
        )
        if actual != product_class:
            raise AssertionError(
                f"12T{base_t} product norm core is {actual}, expected {product_class}"
            )
        families.append(family_from_integral_basis_seed(
            family=f"score700_linear_sign_product_12t{base_t}_{target_t}",
            base_t=base_t,
            target_t=target_t,
            expected_norm_squareclass=product_class,
            base_coefficients=coefficients,
            seed_coefficients=seed_coefficients,
            target_root_counts=tuple(range(0, 25, 4)),
            construction_overgroup=f"C2^11_linear_sign_product_over_12T{base_t}",
            character_name=f"linear_norm_{norm_class}_times_permutation_sign",
            base_root_count=12,
        ))
    return families


_POLYNOMIAL_NORM_CHARACTERS = (
    # base, target, norm core, h(alpha), catalog polynomial
    (69, 18511, 37, (-5, -1, 1),
     (4, -20, -88, 124, 344, -176, -380, 50, 138, -4, -20, 0, 1)),
    (75, 19239, 17, (2, -3, 1),
     (38, 31, -235, -241, 329, 323, -205, -166, 70, 37, -13, -3, 1)),
    (135, 20748, 37, (0, 1, 1),
     (-1, -36, 116, 37, -299, 114, 211, -140, -43, 49, -3, -5, 1)),
    (154, 20829, 2, (0, 2, 3, 1),
     (-4, -32, -20, 248, 257, -476, -480, 152, 184, -12, -24, 0, 1)),
    (219, 22762, 5, (0, -1, 1),
     (-5, -80, 379, -319, -361, 514, 46, -249, 42, 47, -13, -3, 1)),
    (246, 23299, 34, (-1, -2, 1),
     (-9826, -5780, 63019, -47226, -23173, 29864, -1424, -4936, 734, 308, -51, -6, 1)),
    (266, 23814, 13, (0, 3, 1),
     (2096, 12576, 28296, 26760, 3699, -10368, -4832, 1056, 792, -32, -48, 0, 1)),
    # A direct quadratic gives the 2*71 product character on 12T154.
    (154, 20831, 142, (-2, 2, 1),
     (-4, -32, -20, 248, 257, -476, -480, 152, 184, -12, -24, 0, 1)),
)


def polynomial_norm_character_families() -> list[IntegralBasisCharacterFamily]:
    """Return small quadratic/cubic norm characters found in the catalog."""

    families: list[IntegralBasisCharacterFamily] = []
    for base_t, target_t, norm_class, seed, coefficients in _POLYNOMIAL_NORM_CHARACTERS:
        x = _X
        base = Poly(sum(value * x**i for i, value in enumerate(coefficients)), x)
        generator = Poly(sum(value * x**i for i, value in enumerate(seed)), x)
        actual = _squarefree_part(
            int(resultant(base.as_expr(), generator.as_expr(), x))
        )
        if actual != norm_class:
            raise AssertionError(
                f"12T{base_t} polynomial norm core is {actual}, expected {norm_class}"
            )
        families.append(family_from_integral_basis_seed(
            family=f"score700_polynomial_norm_12t{base_t}_{target_t}",
            base_t=base_t,
            target_t=target_t,
            expected_norm_squareclass=norm_class,
            base_coefficients=coefficients,
            seed_coefficients=seed,
            target_root_counts=tuple(range(0, 25, 4)),
            construction_overgroup=f"C2^11_polynomial_norm_character_over_12T{base_t}",
            character_name=f"polynomial_norm_squareclass_{norm_class}",
            base_root_count=12,
        ))

    # 12T135: multiply the norm-37 generator by f'(alpha) (disc core 3709)
    # to obtain the high-value product class 137233 and target 24T20749.
    base_t, target_t, norm_class, seed, coefficients = _POLYNOMIAL_NORM_CHARACTERS[2]
    x = _X
    base = Poly(sum(value * x**i for i, value in enumerate(coefficients)), x)
    generator = Poly(
        sum(value * x**i for i, value in enumerate(seed)) * base.diff().as_expr(),
        x,
    ).rem(base)
    product_seed = tuple(int(generator.nth(i)) for i in range(12))
    product_class = _squarefree_part(
        int(resultant(base.as_expr(), generator.as_expr(), x))
    )
    if product_class != 137233:
        raise AssertionError(f"12T135 product norm core is {product_class}")
    families.append(family_from_integral_basis_seed(
        family="score700_polynomial_sign_product_12t135_20749",
        base_t=135,
        target_t=20749,
        expected_norm_squareclass=137233,
        base_coefficients=coefficients,
        seed_coefficients=product_seed,
        target_root_counts=tuple(range(0, 25, 4)),
        construction_overgroup="C2^11_polynomial_sign_product_over_12T135",
        character_name="norm_37_times_permutation_sign_3709",
        base_root_count=12,
    ))
    return families


def campaign_families() -> list[UnitCharacterFamily]:
    families = (
        crossed_12t210_families()
        + [outer_d_12t210_family()]
        + structured_12t214_families()
        + [catalog_12t156_sign_family()]
    )
    pairs = [
        (family.target_t, root)
        for family in families
        for root in family.target_root_counts
    ]
    if len(pairs) != len(set(pairs)):
        raise AssertionError("score-700 campaign contains duplicate target rows")
    return families


def certify_integral_basis_campaign(
    families: list[IntegralBasisCharacterFamily],
    *,
    manifest_path: Path,
    owned_pairs: set[tuple[int, int]],
    options_per_signature: int,
    coefficient_limit: int = 10**120,
    prime_limit: int = 10000,
) -> list[dict[str, Any]]:
    existing = _load_manifest(manifest_path)
    completed = {
        (int(row["target_t"]), int(row["target_r"]))
        for row in existing
        if row.get("exact_compatibility_proven") and row.get("submission_ready")
    }
    for index, family in enumerate(families, 1):
        roots = tuple(
            root for root in family.target_root_counts
            if (family.target_t, root) not in owned_pairs
            and (family.target_t, root) not in completed
        )
        if not roots:
            continue
        active = replace(family, target_root_counts=roots)
        print(json.dumps({
            "event": "certify_integral_basis_family",
            "family": active.family,
            "index": index,
            "total_families": len(families),
            "target_t": active.target_t,
            "roots": list(roots),
        }), flush=True)
        try:
            candidates = build_integral_basis_character_bank(
                active,
                options_per_signature=options_per_signature,
                coefficient_limit=coefficient_limit,
                prime_limit=prime_limit,
            )
        except Exception as family_error:
            print(json.dumps({
                "event": "integral_basis_family_retry_by_row",
                "family": active.family,
                "error": str(family_error),
            }), flush=True)
            candidates = []
            for root in roots:
                try:
                    candidates.extend(build_integral_basis_character_bank(
                        replace(active, target_root_counts=(root,)),
                        options_per_signature=max(24, options_per_signature),
                        coefficient_limit=coefficient_limit,
                        prime_limit=prime_limit,
                    ))
                except Exception as row_error:
                    print(json.dumps({
                        "event": "integral_basis_row_excluded",
                        "family": active.family,
                        "target_t": active.target_t,
                        "target_r": root,
                        "error": str(row_error),
                    }), flush=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("a", encoding="utf-8") as handle:
            for candidate in candidates:
                handle.write(json.dumps(candidate.to_json(), sort_keys=True) + "\n")
            handle.flush()
        completed.update((candidate.target_t, candidate.target_r) for candidate in candidates)
    return _load_manifest(manifest_path)


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--payload", type=Path, default=DEFAULT_PAYLOAD)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--certify", action="store_true")
    parser.add_argument("--family-prefix")
    parser.add_argument("--options-per-signature", type=int, default=64)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=10000)
    parser.add_argument("--recover-subgroups", action="store_true")
    parser.add_argument("--recovery-all-roots", action="store_true")
    parser.add_argument("--recovery-options-per-signature", type=int, default=8)
    args = parser.parse_args()

    families = campaign_families()
    integral_families = (
        integral_basis_12t156_families()
        + integral_basis_12t243_families()
        + linear_norm_character_families()
        + linear_norm_sign_product_families()
        + polynomial_norm_character_families()
    )
    selected = (
        [family for family in families if family.family.startswith(args.family_prefix)]
        if args.family_prefix
        else families
    )
    selected_integral = (
        [
            family for family in integral_families
            if family.family.startswith(args.family_prefix)
        ]
        if args.family_prefix
        else integral_families
    )
    if args.family_prefix and not selected and not selected_integral:
        parser.error(f"no campaign families match prefix {args.family_prefix!r}")

    owned = known_owned_pairs(args.db)
    if args.certify:
        manifest = certify_campaign(
            selected,
            manifest_path=args.manifest,
            owned_pairs=owned,
            options_per_signature=args.options_per_signature,
        )
        manifest = certify_integral_basis_campaign(
            selected_integral,
            manifest_path=args.manifest,
            owned_pairs=owned,
            options_per_signature=args.options_per_signature,
            coefficient_limit=args.coefficient_limit,
            prime_limit=args.prime_limit,
        )
    else:
        manifest = _load_manifest(args.manifest)
    if args.recover_subgroups or args.recovery_all_roots:
        # Reuse the generic exact overgroup-to-terminal descent.  Explicit
        # norm characters often land in valuable proper Kummer submodules,
        # and those rows should be harvested rather than discarded merely
        # because they miss the largest calibrated character-kernel group.
        from routeA.build_catalog_character_campaign import (
            RankedCatalogFamily,
            recover_catalog_subgroups,
        )

        recovery_families = [
            RankedCatalogFamily(
                family=family,
                live_ceiling=0.0,
                catalog_label=f"explicit_12T{family.base_t}",
            )
            for family in selected_integral
        ]
        manifest = recover_catalog_subgroups(
            recovery_families,
            manifest_path=args.manifest,
            db_path=args.db,
            owned_pairs=owned,
            options_per_signature=args.recovery_options_per_signature,
            coefficient_limit=args.coefficient_limit,
            prime_limit=args.prime_limit,
            all_roots=args.recovery_all_roots,
        )
    payload_rows = write_payload(manifest, args.payload, owned) if manifest else 0
    rows, ceiling, adjusted = forecast(
        manifest,
        db_path=args.db,
        owned_pairs=owned,
    )
    print(json.dumps({
        "families": len(families) + len(integral_families),
        "selected_families": len(selected) + len(selected_integral),
        "certified_manifest_rows": len(manifest),
        "payload_rows": payload_rows,
        "forecast_rows": len(rows),
        "sharing_ceiling": ceiling,
        "discriminant_adjusted_forecast": adjusted,
        "manifest": str(args.manifest),
        "payload": str(args.payload),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
