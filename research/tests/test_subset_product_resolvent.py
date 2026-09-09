from itertools import combinations

import pytest

from routeA.subset_product_resolvent import triple_shift_product_resolvent


def _multiply(left, right):
    output = [0] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            output[i + j] += a * b
    return output


def test_triple_shift_product_resolvent_on_split_quartic():
    roots = (-3, -1, 2, 4)
    source = [1]
    for root in roots:
        source = _multiply(source, [-root, 1])

    expected = [1]
    for triple in combinations(roots, 3):
        value = 1
        for root in triple:
            value *= 1 + 2 * root
        expected = _multiply(expected, [-value, 1])

    assert triple_shift_product_resolvent(source, shift_weight=2) == tuple(expected)


def test_triple_shift_product_resolvent_rejects_zero_weight():
    with pytest.raises(ValueError, match="nonzero"):
        triple_shift_product_resolvent((-1, 0, 0, 1), shift_weight=0)
