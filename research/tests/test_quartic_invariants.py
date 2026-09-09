import sympy

from routeA.quartic_invariants import (
    classify_relative_quartic,
    cubic_resolvent_coefficients,
    depressed_quartic_discriminant,
    frobenius_partition,
    old_form_regime,
    predicted_old_label,
)


def test_old_even_form_regimes_match_forensic_labels():
    assert old_form_regime("pure4") == "D4_linked"
    assert old_form_regime("d4-k3") == "D4_linked"
    assert old_form_regime("c4") == "C4_linked"
    assert old_form_regime("v4-entangled") == "V4_common"
    assert predicted_old_label("d4-k5") == 19036
    assert predicted_old_label("c4") == 17757
    assert predicted_old_label("v4-entangled") == 7181


def test_general_quartic_resolvent_convention():
    a, b, c, d, z = sympy.symbols("a b c d z")
    coefficients = cubic_resolvent_coefficients(a, b, c, d)
    resolvent = sum(value * z**index for index, value in enumerate(coefficients))
    expected = (
        z**3 - b * z**2 + (a * c - 4 * d) * z + 4 * b * d - a**2 * d - c**2
    )
    assert sympy.expand(resolvent - expected) == 0


def test_a4_seed_has_square_discriminant_and_irreducible_resolvent_shape():
    t = sympy.symbols("t")
    p = 6 + 2 * t
    q = -8
    r = t**2 + 2 * t + 9
    disc = sympy.factor(depressed_quartic_discriminant(p, q, r))
    assert disc == 4096 * (t**2 + 3 * t + 9) ** 2


def test_relative_regime_classification():
    assert classify_relative_quartic([3], False).name == "S4"
    assert classify_relative_quartic([3], True).name == "A4"
    assert classify_relative_quartic([1, 2], False).name == "D4"


def test_frobenius_partition():
    # x^4 + x + 1 is irreducible modulo 2.
    assert frobenius_partition([1, 1, 0, 0, 1], 2) == (4,)
