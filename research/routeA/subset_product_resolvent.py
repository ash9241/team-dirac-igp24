"""Exact shifted-product resolvents for three-element root subsets."""

from __future__ import annotations

import math
from typing import Sequence

from routeA.ordered_pair_resolvent import _monic_from_power_sums
from routeA.pair_sum_resolvent import root_power_sums


def triple_shift_product_resolvent(
    coefficients: Sequence[int],
    *,
    shift_weight: int = 1,
) -> tuple[int, ...]:
    """Return ``prod(y-(1+w*a)(1+w*b)(1+w*c))`` over triples.

    The shifted product is a generic symmetric value on unordered
    three-subsets.  Its moments follow from the third elementary symmetric
    function, so the construction avoids enumerating the ``binomial(n, 3)``
    root triples.
    """

    values = tuple(int(value) for value in coefficients)
    degree = len(values) - 1
    weight = int(shift_weight)
    if degree < 3 or values[-1] != 1:
        raise ValueError("polynomial must be monic of degree at least three")
    if weight == 0:
        raise ValueError("shift_weight must be nonzero")

    target_degree = math.comb(degree, 3)
    transformed_polynomial = _affine_root_polynomial(values, weight=weight)
    powers = root_power_sums(transformed_polynomial, 3 * target_degree)
    triple_powers = [target_degree]
    for exponent in range(1, target_degree + 1):
        numerator = (
            powers[exponent] ** 3
            - 3 * powers[exponent] * powers[2 * exponent]
            + 2 * powers[3 * exponent]
        )
        if numerator % 6:
            raise AssertionError("nonintegral triple-product power sum")
        triple_powers.append(numerator // 6)
    return _monic_from_power_sums(triple_powers)


def _affine_root_polynomial(
    coefficients: Sequence[int],
    *,
    weight: int,
) -> tuple[int, ...]:
    """Return the monic polynomial with roots ``1 + weight*a_i``."""

    values = tuple(int(value) for value in coefficients)
    degree = len(values) - 1
    output = [0] * (degree + 1)
    for source_power, coefficient in enumerate(values):
        scale = coefficient * weight ** (degree - source_power)
        for output_power in range(source_power + 1):
            output[output_power] += (
                scale
                * math.comb(source_power, output_power)
                * (-1) ** (source_power - output_power)
            )
    if output[-1] != 1:
        raise AssertionError("affine root polynomial is not monic")
    return tuple(output)
