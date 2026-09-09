"""Lightweight integer constructions for ordered-pair resolvents."""

from __future__ import annotations

import math
from typing import Sequence

from routeA.pair_sum_resolvent import root_power_sums


def ordered_affine_resolvent(
    coefficients: Sequence[int],
    *,
    weight: int = 2,
) -> tuple[int, ...]:
    """Return ``prod(i != j, y-(alpha_i+weight*alpha_j))`` ascending.

    A weight other than ``0``, ``1``, or ``-1`` distinguishes the order of a
    generic root pair and avoids the systematic collisions of sum and
    difference resolvents.
    """

    values = tuple(int(value) for value in coefficients)
    degree = len(values) - 1
    weight = int(weight)
    if degree <= 1 or values[-1] != 1:
        raise ValueError("polynomial must be monic of degree at least two")
    if weight in {-1, 0, 1}:
        raise ValueError("ordered affine weight must not be -1, 0, or 1")
    target_degree = degree * (degree - 1)
    powers = root_power_sums(values, target_degree)
    affine_powers = [target_degree]
    for exponent in range(1, target_degree + 1):
        all_pairs = sum(
            math.comb(exponent, index)
            * weight ** (exponent - index)
            * powers[index]
            * powers[exponent - index]
            for index in range(exponent + 1)
        )
        affine_powers.append(
            all_pairs - (1 + weight) ** exponent * powers[exponent]
        )
    return _monic_from_power_sums(affine_powers)


def ordered_difference_resolvent(coefficients: Sequence[int]) -> tuple[int, ...]:
    """Return ``prod(i != j, y-(alpha_i-alpha_j))`` ascending.

    This symmetric resolvent is useful for diagnostics, but ordered actions
    should normally use :func:`ordered_affine_resolvent` so swapped pairs do
    not collapse together.
    """

    values = tuple(int(value) for value in coefficients)
    degree = len(values) - 1
    if degree <= 1 or values[-1] != 1:
        raise ValueError("polynomial must be monic of degree at least two")
    pair_count = degree * (degree - 1) // 2
    powers = root_power_sums(values, 2 * pair_count)
    squared_powers = [pair_count]
    for exponent in range(1, pair_count + 1):
        moment = 2 * exponent
        ordered = sum(
            (-1) ** (moment - index)
            * math.comb(moment, index)
            * powers[index]
            * powers[moment - index]
            for index in range(moment + 1)
        )
        if ordered % 2:
            raise AssertionError("nonintegral squared-difference power sum")
        squared_powers.append(ordered // 2)
    squared_polynomial = _monic_from_power_sums(squared_powers)
    output = [0] * (2 * pair_count + 1)
    for index, value in enumerate(squared_polynomial):
        output[2 * index] = value
    return tuple(output)


def _monic_from_power_sums(powers: Sequence[int]) -> tuple[int, ...]:
    degree = len(powers) - 1
    if degree < 1 or int(powers[0]) != degree:
        raise ValueError("power-sum sequence has inconsistent degree")
    elementary = [1]
    for index in range(1, degree + 1):
        numerator = sum(
            (1 if power % 2 else -1)
            * elementary[index - power]
            * int(powers[power])
            for power in range(1, index + 1)
        )
        if numerator % index:
            raise AssertionError("nonintegral resolvent coefficient")
        elementary.append(numerator // index)
    output = [0] * (degree + 1)
    for index, value in enumerate(elementary):
        output[degree - index] = (-1) ** index * value
    return tuple(output)
