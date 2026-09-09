import pytest

from routeA.ordered_pair_resolvent import (
    ordered_affine_resolvent,
    ordered_difference_resolvent,
)


def _polynomial_from_roots(roots: list[int]) -> tuple[int, ...]:
    coefficients = [1]
    for root in roots:
        updated = [0] * (len(coefficients) + 1)
        for index, value in enumerate(coefficients):
            updated[index] -= root * value
            updated[index + 1] += value
        coefficients = updated
    return tuple(coefficients)


def test_ordered_difference_resolvent_for_integer_roots() -> None:
    assert ordered_difference_resolvent((6, -5, 1)) == (-1, 0, 1)
    assert ordered_difference_resolvent((-8, 14, -7, 1)) == (
        -36, 0, 49, 0, -14, 0, 1,
    )


def test_ordered_affine_resolvent_distinguishes_pair_order() -> None:
    # Roots 1,2,4 produce alpha_i + 2*alpha_j values 4,5,6,8,9,10.
    assert ordered_affine_resolvent((-8, 14, -7, 1)) == _polynomial_from_roots(
        [4, 5, 6, 8, 9, 10]
    )
    with pytest.raises(ValueError, match="weight"):
        ordered_affine_resolvent((6, -5, 1), weight=1)
