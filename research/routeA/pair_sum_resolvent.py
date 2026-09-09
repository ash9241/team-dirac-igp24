"""Lightweight integer construction of pair-sum resolvents."""

from __future__ import annotations

import math
from typing import Sequence


def root_power_sums(coefficients: Sequence[int], maximum: int) -> list[int]:
    """Return Newton power sums for a monic ascending polynomial."""

    values = tuple(int(value) for value in coefficients)
    degree = len(values) - 1
    if degree <= 0 or values[-1] != 1:
        raise ValueError("polynomial must be monic of positive degree")
    powers = [degree]
    for exponent in range(1, int(maximum) + 1):
        total = 0
        for offset in range(1, min(exponent, degree) + 1):
            coefficient = values[degree - offset]
            if offset == exponent:
                total += offset * coefficient
            else:
                total += coefficient * powers[exponent - offset]
        powers.append(-total)
    return powers


def pair_sum_resolvent(coefficients: Sequence[int]) -> tuple[int, ...]:
    """Return the monic pair-sum resolvent in ascending coefficients."""

    degree = len(coefficients) - 1
    target_degree = degree * (degree - 1) // 2
    powers = root_power_sums(coefficients, target_degree)
    pair_powers = [target_degree]
    for exponent in range(1, target_degree + 1):
        ordered = sum(
            math.comb(exponent, index)
            * powers[index]
            * powers[exponent - index]
            for index in range(exponent + 1)
        )
        numerator = ordered - 2**exponent * powers[exponent]
        if numerator % 2:
            raise AssertionError("nonintegral pair power sum")
        pair_powers.append(numerator // 2)

    elementary = [1]
    for index in range(1, target_degree + 1):
        numerator = sum(
            (1 if power % 2 else -1)
            * elementary[index - power]
            * pair_powers[power]
            for power in range(1, index + 1)
        )
        if numerator % index:
            raise AssertionError("nonintegral resolvent coefficient")
        elementary.append(numerator // index)
    output = [0] * (target_degree + 1)
    for index, value in enumerate(elementary):
        output[target_degree - index] = (-1) ** index * value
    return tuple(output)


def pair_sum_product_resolvent(
    coefficients: Sequence[int],
    *,
    product_weight: int = 1,
) -> tuple[int, ...]:
    """Return the unordered-pair resolvent for ``a+b+w*a*b``.

    The ordinary pair sum can specialize badly (notably for even source
    polynomials, where every opposite-root pair sums to zero).  Adding the
    symmetric product term preserves the same action on unordered index pairs
    while generically separating their values.  The calculation uses

    ``1 + w*(a+b+w*a*b) = (1+w*a)*(1+w*b)``

    and therefore needs only root power sums and exact integer arithmetic.
    A caller should still verify the factor-degree multiplicities against the
    GAP orbit census and try another nonzero weight if a specialization
    collision remains.
    """

    values = tuple(int(value) for value in coefficients)
    degree = len(values) - 1
    weight = int(product_weight)
    if degree <= 1 or values[-1] != 1:
        raise ValueError("polynomial must be monic of degree at least two")
    if weight == 0:
        raise ValueError("product_weight must be nonzero")
    target_degree = degree * (degree - 1) // 2
    powers = root_power_sums(values, 2 * target_degree)

    # U_m is the m-th power sum of the transformed roots 1+w*a_i.
    transformed = []
    for exponent in range(2 * target_degree + 1):
        transformed.append(sum(
            math.comb(exponent, power)
            * weight**power
            * powers[power]
            for power in range(exponent + 1)
        ))

    # Z_m is the m-th power sum of pair products u_i*u_j.
    pair_products = []
    for exponent in range(target_degree + 1):
        numerator = transformed[exponent] ** 2 - transformed[2 * exponent]
        if numerator % 2:
            raise AssertionError("nonintegral transformed pair-product power sum")
        pair_products.append(numerator // 2)

    result_powers = [target_degree]
    for exponent in range(1, target_degree + 1):
        numerator = sum(
            math.comb(exponent, power)
            * (-1) ** (exponent - power)
            * pair_products[power]
            for power in range(exponent + 1)
        )
        denominator = weight**exponent
        if numerator % denominator:
            raise AssertionError("nonintegral pair sum-product power sum")
        result_powers.append(numerator // denominator)

    elementary = [1]
    for index in range(1, target_degree + 1):
        numerator = sum(
            (1 if power % 2 else -1)
            * elementary[index - power]
            * result_powers[power]
            for power in range(1, index + 1)
        )
        if numerator % index:
            raise AssertionError("nonintegral resolvent coefficient")
        elementary.append(numerator // index)
    output = [0] * (target_degree + 1)
    for index, value in enumerate(elementary):
        output[target_degree - index] = (-1) ** index * value
    return tuple(output)
