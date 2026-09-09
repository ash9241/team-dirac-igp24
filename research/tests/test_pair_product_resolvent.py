from routeA.pair_product_resolvent import _square_root_monic
from routeA.pair_sum_resolvent import pair_sum_product_resolvent


def test_square_root_monic_reconstructs_degree_two_square():
    root_desc = [1, -3, 2] + [0] * 64
    root = list(reversed(root_desc))
    square = [0] * 133
    for i, left in enumerate(root):
        for j, right in enumerate(root):
            square[i + j] += left * right
    assert _square_root_monic(list(reversed(square))) == root


def test_pair_sum_product_resolvent_uses_same_unordered_pairs_without_sum_collisions():
    # Roots 1,2,3 give pair values a+b+a*b equal to 5,7,11.
    polynomial = [-6, 11, -6, 1]
    assert pair_sum_product_resolvent(polynomial) == (-385, 167, -23, 1)


def test_pair_sum_product_resolvent_supports_other_nonzero_weights():
    # At product weight 2 the pair values are 7,10,17.
    polynomial = [-6, 11, -6, 1]
    assert pair_sum_product_resolvent(
        polynomial, product_weight=2
    ) == (-1190, 359, -34, 1)
