import pytest
from sympy import Poly, symbols

from routeA.constructions.unit_character_lift import (
    UnitCharacterFamily,
    build_reduced_unit_lift,
    enumerate_unit_generators,
    family_from_polynomials,
)


def test_partial_real_base_limits_requested_signatures() -> None:
    family = UnitCharacterFamily(
        family="partial-real",
        base_t=210,
        target_t=22548,
        expected_norm_squareclass=1598,
        base_coefficients=(1,) + (0,) * 11 + (1,),
        generator_coefficients=(1,),
        target_root_counts=(0, 4, 8, 12, 16),
        base_root_count=8,
    )
    assert family.base_root_count == 8

    with pytest.raises(ValueError, match="twice the real base embeddings"):
        UnitCharacterFamily(
            family="partial-real",
            base_t=210,
            target_t=22548,
            expected_norm_squareclass=1598,
            base_coefficients=(1,) + (0,) * 11 + (1,),
            generator_coefficients=(1,),
            target_root_counts=(20,),
            base_root_count=8,
        )


def test_unit_character_enumeration_covers_every_even_norm_signature() -> None:
    x = symbols("x")
    a = x**3 - 3 * x - 1
    b = x**2 + x
    base = Poly(a**4 - 18 * a**2 * b**2 + 25 * b**4, x)
    generator = Poly(b * (a + 5 * b), x)
    asc = lambda value: tuple(reversed([int(v) for v in Poly(value, x).all_coeffs()]))
    family = family_from_polynomials(
        family="test",
        base_t=130,
        target_t=20525,
        expected_norm_squareclass=2,
        base_coefficients=asc(base),
        generator_coefficients=asc(generator),
    )
    options = enumerate_unit_generators(family, options_per_signature=1)
    assert set(options) == set(range(0, 25, 4))
    assert all(len(value) == 1 for value in options.values())
    spec = build_reduced_unit_lift(family, options[8][0])
    assert spec.target_r == 8
    assert len(spec.coefficients) == 25
    assert spec.coefficients[-1] == 1
