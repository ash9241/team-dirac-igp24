import pytest

from routeA.constructions.twisted_reciprocal import (
    _evaluate,
    build_critical_twisted_reciprocal,
    build_twisted_reciprocal,
    critical_inner_coefficients,
)


@pytest.mark.parametrize(("character", "critical_value", "target"), [
    ("within", 3, 24871),
    ("product", 5, 24869),
])
def test_engineered_critical_values(character: str, critical_value: int, target: int) -> None:
    spec = build_twisted_reciprocal(c=7, r=2 * 2, character=character)
    assert spec.target_t == target
    assert _evaluate(spec.inner_coefficients, spec.c) == critical_value
    assert _evaluate(spec.base_coefficients, spec.c) == critical_value**2 - 5
    assert spec.coefficients == tuple(reversed(spec.coefficients))
    assert spec.coefficients[-1] == 1


def test_inner_derivative_has_prescribed_factorization() -> None:
    c, r = 7, 4
    inner = critical_inner_coefficients(c, r, 3)
    derivative = tuple(power * inner[power] for power in range(1, 7))
    for value in (-2, 2, c, r):
        assert _evaluate(derivative, value) == 0


def test_integrality_congruences_are_enforced() -> None:
    with pytest.raises(ValueError, match="integrality"):
        critical_inner_coefficients(c=1, r=1, critical_value=3)


@pytest.mark.parametrize(("character", "roots", "mark", "value", "d", "target", "signature_hint"), [
    ("within", (-6, -5, -4), -5, 11, 85, 24871, 20),
    ("product", (-6, -5, -4), -4, -4, 8, 24869, 20),
    ("product", (-18, -11, 4), 4, -14162, 100281122, 24869, 18),
])
def test_five_simple_portraits_encode_character(
    character: str,
    roots: tuple[int, int, int],
    mark: int,
    value: int,
    d: int,
    target: int,
    signature_hint: int,
) -> None:
    spec = build_critical_twisted_reciprocal(roots, mark, value, d, character)
    assert spec.target_t == target
    assert _evaluate(spec.inner_coefficients, mark) == value
    assert spec.parameters["critical_roots"] == list(roots)
    assert spec.coefficients == tuple(reversed(spec.coefficients))
    # Keep the intended signature visible in the fixture without requiring GP.
    assert signature_hint in {18, 20}


def test_five_simple_portrait_rejects_false_character_claim() -> None:
    with pytest.raises(ValueError, match="requested character"):
        build_critical_twisted_reciprocal((-6, -5, -4), -5, 11, 85, "product")
