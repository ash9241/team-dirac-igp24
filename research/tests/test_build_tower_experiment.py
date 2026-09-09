import random

from routeA.build_tower_experiment import random_polynomial


def test_random_tower_polynomial_is_nonzero_and_trimmed():
    rng = random.Random(7)
    for _ in range(20):
        value = random_polynomial(rng, 2)
        assert 1 <= len(value) <= 3
        assert any(value)
        assert value[-1] != 0
