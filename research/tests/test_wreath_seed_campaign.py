from routeA.build_wreath_seed_campaign import (
    full_wreath_targets,
    linear_wreath_families,
)
from routeA.build_t00035_campaign import _squarefree_part


def test_full_wreath_map_and_linear_seed_norms_are_exact() -> None:
    targets = full_wreath_targets()
    assert len(targets) == 301
    assert targets[154] == 21874

    ranked = linear_wreath_families(shifts=range(-1, 2), bases=[105])
    assert len(ranked) == 3
    for item in ranked:
        family = item.family
        shift = -int(family.seed_coefficients[0])
        norm = sum(
            coefficient * shift**power
            for power, coefficient in enumerate(family.base_coefficients)
        )
        assert family.expected_norm_squareclass == abs(_squarefree_part(norm))
        assert family.target_t == targets[105]
        assert family.construction_overgroup == "full_C2_wreath_over_12T105"
