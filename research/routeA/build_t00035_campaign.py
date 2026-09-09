#!/usr/bin/env python3
"""Build the exact one-shot campaign for leaderboard target IGP24-T00035.

The campaign combines three independently calibrated sources of degree-24
fields:

* C3/S3 cubic-over-quartic character lifts;
* C2-over-sextic product-character lifts;
* direct cubic/quartic compositum sign characters.

Every emitted row is irreducible, has the requested real signature and norm
character, and excludes every transitive maximal subgroup of its claimed 24T
group.  Certification is incremental so a long run can be resumed safely.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from sympy import Poly, Rational, resultant, symbols

from routeA.build_funny_sock_campaign import PROJECT, known_owned_pairs
from routeA.constructions.unit_character_lift import (
    UnitCharacterFamily,
    UnitCharacterLiftCandidate,
    build_unit_character_bank,
    family_from_polynomials,
)
from routeA.ledger import DEFAULT_DB, Ledger
from routeA.scheduler import discriminant_score_ratio


_X, _Z = symbols("x z")
DEFAULT_MANIFEST = PROJECT / "routeA" / "data" / "t00035_campaign.jsonl"
DEFAULT_PAYLOAD = PROJECT / "routeA" / "t00035_campaign.txt"


def _asc(value: Any, modulus: Poly | None = None) -> tuple[int, ...]:
    polynomial = Poly(value, _X, domain="QQ")
    if modulus is not None:
        polynomial = polynomial.rem(modulus)
    denominator = 1
    for coefficient in polynomial.all_coeffs():
        denominator = _lcm(denominator, int(coefficient.q))
    polynomial = Poly(polynomial.as_expr() * denominator, _X, domain="ZZ")
    return tuple(reversed(tuple(int(value) for value in polynomial.all_coeffs())))


def _lcm(left: int, right: int) -> int:
    from math import gcd

    return abs(left * right) // gcd(left, right)


def _family(
    *,
    name: str,
    base_t: int,
    target_t: int,
    norm: int,
    base: Any,
    generator: Any,
    roots: Iterable[int] = range(0, 25, 4),
    character: str,
    overgroup: str,
) -> UnitCharacterFamily:
    base_poly = Poly(base, _X, domain="ZZ")
    return family_from_polynomials(
        family=name,
        base_t=base_t,
        target_t=target_t,
        expected_norm_squareclass=norm,
        base_coefficients=_asc(base_poly),
        generator_coefficients=_asc(generator, base_poly),
        target_root_counts=roots,
        construction_overgroup=overgroup,
        character_name=character,
    )


def cubic_quartic_character_families() -> list[UnitCharacterFamily]:
    x = _X
    a = x**3 - 3 * x - 1
    b = x**2 + x
    positive_c3 = (x**2 + 1) * ((x + 1) ** 2 + 1)
    positive_s3 = (x - 4) ** 2 + 36
    c4_hom = a**4 - a**3 * b - 6 * a**2 * b**2 + a * b**3 + b**4
    s4_hom = (
        a**4 + 7 * a**3 * b + 7 * a**2 * b**2
        - 8 * a * b**3 - 8 * b**4
    )
    families = [
        _family(
            name="t00035_c3_v4_character",
            base_t=130,
            target_t=20525,
            norm=2,
            base=a**4 - 18 * a**2 * b**2 + 25 * b**4,
            generator=b * (a + 5 * b) * positive_c3,
            character="v4_nontrivial",
            overgroup="C2^11_character_over_C3_wr_V4",
        ),
        _family(
            name="t00035_c3_d4_class_2",
            base_t=167,
            target_t=21570,
            norm=2,
            base=(a**2 - 105 * b**2) ** 2 - 7200 * b**4,
            generator=b * (a + 15 * b) * positive_c3,
            character="d4_class_2",
            overgroup="C2^11_character_over_C3_wr_D4",
        ),
        _family(
            name="t00035_c3_d4_class_17",
            base_t=167,
            target_t=21572,
            norm=17,
            base=(a**2 - 105 * b**2) ** 2 - 7200 * b**4,
            generator=b * a * positive_c3,
            character="d4_class_17",
            overgroup="C2^11_character_over_C3_wr_D4",
        ),
        _family(
            name="t00035_c3_d4_class_34",
            base_t=167,
            target_t=21571,
            norm=34,
            base=(a**2 - 105 * b**2) ** 2 - 7200 * b**4,
            generator=b * (a + 17 * b) * positive_c3,
            character="d4_class_34",
            overgroup="C2^11_character_over_C3_wr_D4",
        ),
        _family(
            name="t00035_c3_c4_character",
            base_t=131,
            target_t=20528,
            norm=17,
            base=c4_hom,
            generator=(4 * a**3 - 3 * a**2 * b - 12 * a * b**2 + b**3) * positive_c3,
            character="c4_quadratic",
            overgroup="C2^11_character_over_C3_wr_C4",
        ),
        _family(
            name="t00035_c3_s4_character",
            base_t=231,
            target_t=23115,
            norm=15542,
            base=s4_hom,
            generator=(4 * a**3 + 21 * a**2 * b + 14 * a * b**2 - 8 * b**3) * positive_c3,
            character="s4_sign",
            overgroup="C2^11_character_over_C3_wr_S4",
        ),
    ]

    q_v4 = x**3 - 12 * x - 8
    base_v4 = q_v4**4 - 72 * q_v4**2 + 1024
    q_v4_prime = Poly(q_v4, x).diff().as_expr()
    families.extend([
        _family(
            name="t00035_s3_v4_outer",
            base_t=261,
            target_t=23796,
            norm=2,
            base=base_v4,
            generator=(q_v4 - 4) * positive_s3,
            character="v4_outer",
            overgroup="C2^11_character_over_S3_wr_V4",
        ),
        _family(
            name="t00035_s3_v4_inner",
            base_t=261,
            target_t=23799,
            norm=569,
            base=base_v4,
            generator=q_v4_prime * positive_s3,
            character="s3_inner",
            overgroup="C2^11_character_over_S3_wr_V4",
        ),
        _family(
            name="t00035_s3_v4_product",
            base_t=261,
            target_t=23797,
            norm=1138,
            base=base_v4,
            generator=q_v4_prime * (q_v4 - 4) * positive_s3,
            character="inner_times_v4",
            overgroup="C2^11_character_over_S3_wr_V4",
        ),
    ])

    q = x**3 - 12 * x - 2
    q_prime = Poly(q, x).diff().as_expr()
    base_d4 = (q**2 - 105) ** 2 - 7200
    for name, target, norm, generator, character in (
        ("inner", 24151, 4895849, q_prime, "s3_inner"),
        ("inner_2", 24153, 9791698, q_prime * (q - 15), "inner_times_d4_2"),
        ("inner_34", 24155, 166458866, q_prime * (q - 17), "inner_times_d4_34"),
        ("inner_17", 24157, 83229433, q_prime * q, "inner_times_d4_17"),
    ):
        families.append(_family(
            name=f"t00035_s3_d4_{name}",
            base_t=274,
            target_t=target,
            norm=norm,
            base=base_d4,
            generator=generator * positive_s3,
            character=character,
            overgroup="C2^11_character_over_S3_wr_D4",
        ))

    outer_c4 = q**4 - q**3 - 6 * q**2 + q + 1
    outer_c4_prime = 4 * q**3 - 3 * q**2 - 12 * q + 1
    for name, target, norm, generator, character in (
        ("inner", 23809, 3756418817, q_prime, "s3_inner"),
        ("outer", 23810, 17, outer_c4_prime, "c4_outer"),
        ("product", 23811, 63859119889, q_prime * outer_c4_prime, "inner_times_c4"),
    ):
        families.append(_family(
            name=f"t00035_s3_c4_{name}",
            base_t=264,
            target_t=target,
            norm=norm,
            base=outer_c4,
            generator=generator * positive_s3,
            character=character,
            overgroup="C2^11_character_over_S3_wr_C4",
        ))

    outer_a4 = q**4 - q**3 - 24 * q**2 + 19 * q + 117
    families.append(_family(
        name="t00035_s3_a4_inner",
        base_t=280,
        target_t=24332,
        norm=358122473,
        base=outer_a4,
        generator=q_prime * positive_s3,
        character="s3_inner",
        overgroup="C2^11_character_over_S3_wr_A4",
    ))

    outer_s4 = q**4 + 7 * q**3 + 7 * q**2 - 8 * q - 8
    outer_s4_prime = 4 * q**3 + 21 * q**2 + 14 * q - 8
    for name, target, norm, generator, character in (
        ("inner", 24503, 244909441, q_prime, "s3_inner"),
        ("outer", 24500, 15542, outer_s4_prime, "s4_outer"),
        ("product", 24501, 3806382532022, q_prime * outer_s4_prime, "inner_times_s4"),
    ):
        families.append(_family(
            name=f"t00035_s3_s4_{name}",
            base_t=289,
            target_t=target,
            norm=norm,
            base=outer_s4,
            generator=generator * positive_s3,
            character=character,
            overgroup="C2^11_character_over_S3_wr_S4",
        ))
    return families


_SEXTICS: dict[int, tuple[tuple[int, ...], int, Any]] = {
    1: ((-1, -2, 7, 2, -7, -1, 1), 6, -_X**11 - _X**10 + 36*_X**9 + 18*_X**8 - 505*_X**7 - 47*_X**6 + 3445*_X**5 - 810*_X**4 - 11410*_X**3 + 5678*_X**2 + 14647*_X - 10326),
    2: ((1, -5, 0, 9, -2, -3, 1), 3, _X**11 - 19*_X**9 + 2*_X**8 + 139*_X**7 - 28*_X**6 - 487*_X**5 + 141*_X**4 + 811*_X**3 - 297*_X**2 - 507*_X + 212),
    3: ((-1, -3, 12, 1, -10, -1, 1), 5, (-_X**11 - 33*_X**10 + 36*_X**9 + 865*_X**8 - 451*_X**7 - 8678*_X**6 + 2560*_X**5 + 41691*_X**4 - 6690*_X**3 - 95837*_X**2 + 6513*_X + 84233) / 17),
    5: ((1, -5, 4, 7, -6, -1, 1), 4, 26*_X**11 + 64*_X**10 - 492*_X**9 - 1212*_X**8 + 3613*_X**7 + 8908*_X**6 - 12789*_X**5 - 31561*_X**4 + 21616*_X**3 + 53394*_X**2 - 13756*_X - 34007),
    6: ((-1, -2, 4, 5, -4, -2, 1), 6, _X**11 - 32*_X**9 - _X**8 + 405*_X**7 + 26*_X**6 - 2534*_X**5 - 247*_X**4 + 7837*_X**3 + 1017*_X**2 - 9583*_X - 1532),
    8: ((3, -54, 42, 15, -14, -1, 1), 6, (-3896*_X**11 - 11773*_X**10 + 108604*_X**9 + 328190*_X**8 - 1175244*_X**7 - 3551550*_X**6 + 6142500*_X**5 + 18562839*_X**4 - 15437556*_X**3 - 46653711*_X**2 + 14896284*_X + 45018753) / 12),
    9: ((1, 14, 32, 8, -10, -2, 1), 3, _X + 1),
    11: ((1, -4, -1, 9, -2, -3, 1), 4, _X**8 + _X**7 - 17*_X**6 - 13*_X**5 + 106*_X**4 + 54*_X**3 - 286*_X**2 - 71*_X + 279),
    13: ((1, -5, 2, 8, -4, -2, 1), 4, -2*_X**11 - 3*_X**10 + 42*_X**9 + 65*_X**8 - 342*_X**7 - 549*_X**6 + 1345*_X**5 + 2251*_X**4 - 2546*_X**3 - 4460*_X**2 + 1851*_X + 3399),
    14: ((-69, 70, 90, -9, -18, 0, 1), 4, 2*_X**11 + 7*_X**10 - 33*_X**9 - 124*_X**8 + 196*_X**7 + 810*_X**6 - 527*_X**5 - 2439*_X**4 + 662*_X**3 + 3400*_X**2 - 316*_X - 1771),
    16: ((-1, -2, 5, 4, -5, -1, 1), 7, 2*_X**11 - 2*_X**10 - 78*_X**9 + 82*_X**8 + 1205*_X**7 - 1326*_X**6 - 9217*_X**5 + 10585*_X**4 + 34907*_X**3 - 41762*_X**2 - 52371*_X + 65224),
}

_SEXTIC_BASE_T = {1: 134, 2: 135, 3: 193, 5: 208, 6: 222, 8: 224, 9: 240, 11: 250, 13: 260, 14: 270, 16: 293}
_SEXTIC_PRODUCT_TARGET = {1: 20747, 2: 20749, 3: 21909, 5: 22404, 6: 22801, 8: 22808, 9: 23215, 11: 23447, 13: 23756, 14: 24075, 16: 24561}
_SEXTIC_OUTER_TARGET = {8: 22807, 13: 23755, 14: 24076, 16: 24560}


def _squarefree_part(value: int) -> int:
    from sympy import factorint

    sign = -1 if value < 0 else 1
    output = sign
    for prime, exponent in factorint(abs(int(value))).items():
        if exponent % 2:
            output *= int(prime)
    return output


def sextic_character_families() -> list[UnitCharacterFamily]:
    families: list[UnitCharacterFamily] = []
    for sextic_t, (coefficients, shift, norm_generator) in _SEXTICS.items():
        outer = Poly(sum(value * _X**i for i, value in enumerate(coefficients)), _X)
        base = Poly(outer.as_expr().subs(_X, _X**2 - shift), _X)
        outer_class = _squarefree_part(int(outer.discriminant()))
        inner_class = _squarefree_part(int(outer.eval(-shift)))
        product_class = _squarefree_part(outer_class * inner_class)
        families.append(_family(
            name=f"t00035_c2wr_6t{sextic_t}_product",
            base_t=_SEXTIC_BASE_T[sextic_t],
            target_t=_SEXTIC_PRODUCT_TARGET[sextic_t],
            norm=product_class,
            base=base,
            generator=_X * norm_generator,
            roots=(4, 8, 12, 16, 20),
            character="inner_times_sextic_sign",
            overgroup=f"C2^11_character_over_C2_wr_6T{sextic_t}",
        ))
        if sextic_t in _SEXTIC_OUTER_TARGET:
            families.append(_family(
                name=f"t00035_c2wr_6t{sextic_t}_outer",
                base_t=_SEXTIC_BASE_T[sextic_t],
                target_t=_SEXTIC_OUTER_TARGET[sextic_t],
                norm=outer_class,
                base=base,
                generator=norm_generator,
                roots=(4, 8, 12, 16, 20),
                character="sextic_sign",
                overgroup=f"C2^11_character_over_C2_wr_6T{sextic_t}",
            ))
    return families


def direct_product_families() -> list[UnitCharacterFamily]:
    cubics = {
        1: _Z**3 - 3 * _Z - 1,
        2: _Z**3 - 12 * _Z - 2,
    }
    quartics = {
        1: _X**4 - _X**3 - 6 * _X**2 + _X + 1,
        2: _X**4 - 10 * _X**2 + 1,
        3: (_X**2 - 105) ** 2 - 7200,
        4: _X**4 - _X**3 - 24 * _X**2 + 19 * _X + 117,
        5: _X**4 + 7 * _X**3 + 7 * _X**2 - 8 * _X - 8,
    }
    metadata = {
        (1, 1): (1, 12726, 17),
        (1, 2): (2, 12729, 1),
        (1, 3): (14, 14776, 17),
        (1, 4): (20, 16122, 1),
        (1, 5): (45, 18038, 15542),
        (2, 1): (11, 14763, 17),
        (2, 2): (10, 14760, 1),
        (2, 3): (28, 16727, 17),
        (2, 4): (43, 18034, 1),
        (2, 5): (83, 19351, 15542),
    }
    families = []
    for (cubic_t, quartic_t), (base_t, target_t, norm) in metadata.items():
        base = Poly(
            resultant(cubics[cubic_t], quartics[quartic_t].subs(_X, _X - _Z), _Z),
            _X,
        )
        families.append(_family(
            name=f"t00035_direct_3t{cubic_t}_4t{quartic_t}_sign",
            base_t=base_t,
            target_t=target_t,
            norm=norm,
            base=base,
            generator=base.diff().as_expr(),
            character="degree12_permutation_sign",
            overgroup=f"C2^11_character_over_3T{cubic_t}_times_4T{quartic_t}",
        ))
    return families


_CATALOG_SIGN_FIELDS: dict[int, tuple[int, tuple[int, ...]]] = {
    218: (22756, (217152, -994304, 455488, 1664960, 352880, -382272, -133760, 18480, 9768, -88, -220, -4, 1)),
    175: (21593, (1, 7, 4, -48, -44, 117, 76, -125, -21, 53, -9, -4, 1)),
    44: (18035, (64, -560, 1356, -288, -2180, 1612, 795, -972, 74, 130, -26, -4, 1)),
    301: (24971, (-2, 5, 18, -55, -20, 149, -66, -93, 59, 18, -14, -1, 1)),
    288: (24492, (4725369, 15062247, 15376778, 5644029, -383104, -745649, -109038, 27019, 6836, -296, -141, 0, 1)),
    73: (19178, (1, -9, 21, 18, -118, 88, 102, -134, -1, 47, -12, -3, 1)),
    96: (19689, (43, -406, 1215, -1050, -911, 1552, 88, -664, 59, 110, -15, -6, 1)),
    115: (19749, (454, -1914, 2184, 1294, -4602, 3112, 79, -877, 234, 67, -30, -1, 1)),
    258: (23691, (21, -390, 2001, -1469, -2670, 3233, 32, -1131, 284, 101, -39, -1, 1)),
    205: (22398, (-122123, -313842, -203954, 67246, 100217, 5452, -16998, -2246, 1386, 172, -58, -4, 1)),
    61: (18496, (500, 0, -3900, 0, 5160, 0, -2236, 0, 421, 0, -35, 0, 1)),
    64: (18501, (-171, 612, 473, -1638, -54, 1340, -253, -446, 124, 63, -20, -3, 1)),
    127: (20440, (-1737, 12342, -22588, -1930, 28581, -4400, -12100, -164, 1451, 62, -66, -2, 1)),
    116: (20312, (1, 18, 123, 392, 546, 114, -399, -246, 78, 68, -9, -6, 1)),
    213: (22556, (82, 792, 2448, 2892, 480, -1512, -796, 252, 216, -14, -24, 0, 1)),
    292: (24548, (1, 4, -8, -31, 22, 73, -30, -67, 22, 25, -8, -3, 1)),
    177: (21597, (14, -174, -1440, 589, 2898, -576, -1825, 216, 450, -27, -42, 0, 1)),
    151: (20818, (-2, -64, -198, -74, 331, 260, -199, -187, 58, 52, -10, -5, 1)),
    238: (23209, (-79, -372, 69, 1115, 438, -948, -571, 262, 207, -18, -25, 0, 1)),
    12: (14766, (1528, 3128, -1696, -4788, 1196, 2820, -698, -730, 204, 81, -25, -3, 1)),
}


def catalog_sign_families() -> list[UnitCharacterFamily]:
    """Low-discriminant totally real fields for untouched degree-12 groups."""

    families = []
    for base_t, (target_t, coefficients) in _CATALOG_SIGN_FIELDS.items():
        base = Poly(sum(value * _X**i for i, value in enumerate(coefficients)), _X)
        families.append(_family(
            name=f"t00035_catalog_12t{base_t}_sign",
            base_t=base_t,
            target_t=target_t,
            norm=_squarefree_part(int(base.discriminant())),
            base=base,
            generator=base.diff().as_expr(),
            character="degree12_permutation_sign",
            overgroup=f"C2^11_discriminant_character_over_12T{base_t}",
        ))
    return families


def campaign_families() -> list[UnitCharacterFamily]:
    families = (
        cubic_quartic_character_families()
        + sextic_character_families()
        + direct_product_families()
        + catalog_sign_families()
    )
    pairs = [
        (family.target_t, root)
        for family in families
        for root in family.target_root_counts
    ]
    if len(pairs) != len(set(pairs)):
        raise AssertionError("T00035 campaign contains duplicate target rows")
    return families


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _append_candidates(path: Path, candidates: Sequence[UnitCharacterLiftCandidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate.to_json(), sort_keys=True) + "\n")
        handle.flush()


def certify_campaign(
    families: Sequence[UnitCharacterFamily],
    *,
    manifest_path: Path,
    owned_pairs: set[tuple[int, int]],
    options_per_signature: int = 16,
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
        print(
            json.dumps({
                "event": "certify_family",
                "family": active.family,
                "index": index,
                "total_families": len(families),
                "target_t": active.target_t,
                "roots": list(roots),
            }),
            flush=True,
        )
        try:
            candidates = build_unit_character_bank(
                active,
                options_per_signature=options_per_signature,
            )
        except Exception as family_error:
            print(json.dumps({
                "event": "family_retry_by_row",
                "family": active.family,
                "error": str(family_error),
            }), flush=True)
            candidates = []
            for root in roots:
                try:
                    candidates.extend(build_unit_character_bank(
                        replace(active, target_root_counts=(root,)),
                        options_per_signature=max(24, options_per_signature),
                    ))
                except Exception as row_error:
                    print(json.dumps({
                        "event": "row_excluded",
                        "family": active.family,
                        "target_t": active.target_t,
                        "target_r": root,
                        "error": str(row_error),
                    }), flush=True)
        _append_candidates(manifest_path, candidates)
        completed.update((candidate.target_t, candidate.target_r) for candidate in candidates)
    return _load_manifest(manifest_path)


def forecast(
    manifest: Sequence[dict[str, Any]],
    *,
    db_path: str | Path = DEFAULT_DB,
    owned_pairs: Iterable[tuple[int, int]] = (),
) -> tuple[list[dict[str, Any]], float, float]:
    owned = set(owned_pairs)
    with Ledger(db_path) as ledger:
        targets = ledger.latest_targets()
    rows: list[dict[str, Any]] = []
    ceiling = adjusted = 0.0
    selected: dict[tuple[int, int], dict[str, Any]] = {}
    for candidate in manifest:
        pair = int(candidate["target_t"]), int(candidate["target_r"])
        if pair in owned or pair not in targets:
            continue
        previous = selected.get(pair)
        candidate_disc = int(
            candidate.get("field_disc_abs")
            or candidate.get("estimated_nfdisc_abs")
        )
        if previous is None:
            selected[pair] = candidate
            continue
        previous_disc = int(
            previous.get("field_disc_abs")
            or previous.get("estimated_nfdisc_abs")
        )
        if candidate_disc < previous_disc:
            selected[pair] = candidate

    for pair, candidate in sorted(selected.items()):
        target = targets[pair]
        if bool(target["baseline"]):
            continue
        points = 2.0 ** (-int(target["team_count"]))
        reference = target["minimum_disc_abs"]
        candidate_disc = int(
            candidate.get("field_disc_abs")
            or candidate.get("estimated_nfdisc_abs")
        )
        ratio = 1.0 if int(target["team_count"]) == 0 else (
            discriminant_score_ratio(int(reference), candidate_disc)
            if reference else 0.0
        )
        row = {
            "target_t": pair[0],
            "target_r": pair[1],
            "team_count": int(target["team_count"]),
            "sharing_ceiling": points,
            "discriminant_ratio": ratio,
            "forecast_points": points * ratio,
            "candidate_disc_abs": candidate_disc,
            "reference_disc_abs": reference,
        }
        rows.append(row)
        ceiling += points
        adjusted += points * ratio
    return rows, ceiling, adjusted


def write_payload(manifest: Sequence[dict[str, Any]], path: Path, owned: set[tuple[int, int]]) -> int:
    selected: dict[tuple[int, int], dict[str, Any]] = {}
    for row in manifest:
        pair = int(row["target_t"]), int(row["target_r"])
        if pair in owned or not row.get("exact_compatibility_proven") or not row.get("submission_ready"):
            continue
        previous = selected.get(pair)
        if previous is None or int(row["field_disc_abs"]) < int(previous["field_disc_abs"]):
            selected[pair] = row
    ordered = sorted(selected.values(), key=lambda row: (int(row["target_t"]), int(row["target_r"])))
    path.write_text("\n".join(str(row["coefficients"]) for row in ordered) + "\n", encoding="utf-8")
    return len(ordered)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--payload", type=Path, default=DEFAULT_PAYLOAD)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--certify", action="store_true")
    parser.add_argument(
        "--family-prefix",
        help="certify only families whose names start with this prefix",
    )
    parser.add_argument("--options-per-signature", type=int, default=16)
    args = parser.parse_args()

    families = campaign_families()
    selected_families = (
        [family for family in families if family.family.startswith(args.family_prefix)]
        if args.family_prefix
        else families
    )
    if args.family_prefix and not selected_families:
        parser.error(f"no campaign families match prefix {args.family_prefix!r}")
    owned = known_owned_pairs(args.db)
    if args.certify:
        manifest = certify_campaign(
            selected_families,
            manifest_path=args.manifest,
            owned_pairs=owned,
            options_per_signature=args.options_per_signature,
        )
    else:
        manifest = _load_manifest(args.manifest)
    payload_rows = write_payload(manifest, args.payload, owned) if manifest else 0
    forecast_rows, ceiling, adjusted = forecast(manifest, db_path=args.db, owned_pairs=owned)
    print(json.dumps({
        "families": len(families),
        "selected_families": len(selected_families),
        "certified_manifest_rows": len(manifest),
        "payload_rows": payload_rows,
        "sharing_ceiling": ceiling,
        "discriminant_adjusted_forecast": adjusted,
        "forecast_rows": len(forecast_rows),
        "manifest": str(args.manifest),
        "payload": str(args.payload),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
