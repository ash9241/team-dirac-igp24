from fractions import Fraction

from routeA.build_discovered_character_campaign import _discovery_seed, _pilot_roots
from routeA.discover_product_characters import (
    _linear_factor_product,
    _squarefree_product,
    candidate_product_seeds,
)


def test_squarefree_product_cancels_common_primes() -> None:
    assert _squarefree_product(30, 42) == 35
    assert _squarefree_product(-30, 42) == -35


def test_linear_factor_product_uses_ascending_coefficients() -> None:
    # (x + 1) * x * (x - 2) = x^3 - x^2 - 2x
    assert _linear_factor_product((-1, 0, 2)) == (0, -2, -1, 1)


def test_product_prefilter_recovers_known_and_new_character_norms() -> None:
    seeds = candidate_product_seeds(shift_min=-10, shift_max=10)
    triples = {
        (int(row["base_t"]), int(row["norm_squareclass"])):
        tuple(int(value) for value in row["factor_shifts"])
        for row in seeds
    }
    assert (49, 2) in triples
    assert (135, 37) in triples
    assert (219, 5) in triples
    assert (266, 13) in triples


def test_discovery_seed_accepts_rational_norm_equation_coordinates() -> None:
    seed, tag = _discovery_seed({
        "seed_coefficients": ["1/3", "-2/5"],
        "norm_squareclass": -5,
        "seed_tag": "normeq_-5_s11",
    })
    assert seed == (Fraction(1, 3), Fraction(-2, 5))
    assert tag == "normeq__5_s11"


def test_pilot_root_respects_norm_sign_parity() -> None:
    assert _pilot_roots(5) == (12,)
    assert _pilot_roots(-5) == (10,)
