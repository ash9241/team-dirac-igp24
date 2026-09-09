from routeA.tower_quotient_screen import (
    compatible_degree24_labels,
    cycle_index_order,
    cycle_index_sign,
    support_compatible_labels,
)


def test_support_compatibility_never_uses_likelihood_cutoff():
    indices = {
        1: {"1.1.2.2": 0.1, "6": 0.9},
        2: {"1.1.2.2": 0.9},
        3: {"6": 1.0},
    }
    assert support_compatible_labels({"1,1,2,2"}, indices) == (1, 2)


def test_cycle_index_recovers_group_order_and_parity():
    assert cycle_index_order({"1.1.1": 1 / 6, "3": 2 / 6, "1.2": 3 / 6}, 3) == 6
    assert cycle_index_sign({"1.1.1": 1 / 6, "3": 2 / 6, "1.2": 3 / 6}, 3) == -1
    assert cycle_index_sign({"1.1.1": 1 / 3, "3": 2 / 3}, 3) == 1


def test_degree24_compatibility_requires_full_quotient_chain():
    census = [
        {
            "t": 10,
            "block_sizes": [2, 4, 8],
            "block_quotients": [
                {"block_size": 8, "quotient_degree": 3, "quotient_t": 1},
                {"block_size": 4, "quotient_degree": 6, "quotient_t": 7},
                {"block_size": 2, "quotient_degree": 12, "quotient_t": 50},
            ],
        },
        {
            "t": 11,
            "block_sizes": [2, 4, 8],
            "block_quotients": [
                {"block_size": 8, "quotient_degree": 3, "quotient_t": 2},
                {"block_size": 4, "quotient_degree": 6, "quotient_t": 7},
                {"block_size": 2, "quotient_degree": 12, "quotient_t": 50},
            ],
        },
    ]
    assert compatible_degree24_labels(
        census, quotient3=1, quotient6=[7], quotient12=[50]
    ) == (10,)
