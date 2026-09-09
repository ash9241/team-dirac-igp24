from routeA.constructions.cubic_tower_reciprocal import (
    build_fiber_cubic_tower,
    build_product_cubic_tower,
    build_within_cubic_tower,
)
from routeA.constructions.twisted_reciprocal import _evaluate


def test_product_cubic_tower_mixed_critical_points() -> None:
    spec = build_product_cubic_tower(m=1, s=1, k=1)
    derivative = tuple(i * spec.cubic_coefficients[i] for i in range(1, 4))
    assert _evaluate(spec.cubic_coefficients, -2) == 0
    assert _evaluate(derivative, 2) == 0
    assert _evaluate(spec.base_coefficients, spec.critical_root) == spec.d
    assert spec.target_t == 24155
    assert spec.coefficients == tuple(reversed(spec.coefficients))


def test_within_cubic_tower_mixed_critical_points() -> None:
    spec = build_within_cubic_tower(m=1, s=1, W=2)
    assert _evaluate(spec.base_coefficients, spec.critical_root) == 1
    assert spec.target_t == 24157
    assert spec.coefficients[0] == spec.coefficients[-1] == 1


def test_fiber_cubic_tower_matches_endpoint_and_fiber_characters() -> None:
    spec = build_fiber_cubic_tower(m=1, d=14790)
    endpoint_at_minus_two = _evaluate(spec.base_coefficients, -2)
    fiber_critical_value = _evaluate(spec.base_coefficients, spec.critical_root)
    assert endpoint_at_minus_two == fiber_critical_value
    assert spec.target_t == 24151
    assert spec.parameters == {"m": 1, "h": -392, "d": 14790}
    assert spec.coefficients == tuple(reversed(spec.coefficients))
