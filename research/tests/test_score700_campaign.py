from routeA.build_score700_campaign import (
    campaign_families,
    integral_basis_12t156_families,
    integral_basis_12t243_families,
    linear_norm_character_families,
    linear_norm_sign_product_families,
    polynomial_norm_character_families,
)


def test_score700_campaign_has_calibrated_unique_rows() -> None:
    families = campaign_families()
    assert len(families) == 9

    by_target = {family.target_t: family for family in families}
    assert by_target[22548].expected_norm_squareclass == -1598
    assert by_target[22548].base_root_count == 8
    assert by_target[22548].target_root_counts == (2, 6, 10, 14)
    assert by_target[22544].expected_norm_squareclass == -799
    assert by_target[22544].base_root_count == 8
    assert by_target[22547].expected_norm_squareclass == 1585
    assert by_target[22547].base_root_count == 12
    assert by_target[22560].expected_norm_squareclass == 10
    assert by_target[22560].target_root_counts == (0, 4, 8, 12, 16)
    assert by_target[22562].expected_norm_squareclass == 26
    assert by_target[22563].expected_norm_squareclass == -1910
    assert by_target[22564].expected_norm_squareclass == -191
    assert by_target[22561].expected_norm_squareclass == -4966
    assert all(by_target[t].base_root_count == 8 for t in range(22560, 22565))
    assert by_target[21376].expected_norm_squareclass == 29

    pairs = [
        (family.target_t, root)
        for family in families
        for root in family.target_root_counts
    ]
    assert len(pairs) == len(set(pairs)) == 44


def test_score700_integral_basis_families_are_calibrated() -> None:
    families = integral_basis_12t156_families()
    assert len(families) == 2
    by_target = {family.target_t: family for family in families}
    assert by_target[21372].expected_norm_squareclass == 5
    assert by_target[21374].expected_norm_squareclass == 505
    assert all(family.base_t == 156 for family in families)
    assert all(family.target_root_counts == tuple(range(0, 25, 4)) for family in families)

    families_243 = integral_basis_12t243_families()
    assert len(families_243) == 1
    assert families_243[0].base_t == 243
    assert families_243[0].target_t == 23290
    assert families_243[0].expected_norm_squareclass == 19

    linear = linear_norm_character_families()
    assert len(linear) == 8
    assert {(family.base_t, family.target_t) for family in linear} == {
        (52, 18475), (109, 19725), (136, 20754), (173, 21589),
        (209, 22540), (215, 22568), (216, 22571), (262, 23803),
    }
    products = linear_norm_sign_product_families()
    assert {(family.base_t, family.target_t, family.expected_norm_squareclass)
            for family in products} == {
        (52, 18473, 158),
        (209, 22541, 61),
        (262, 23802, 205),
    }
    polynomial = polynomial_norm_character_families()
    assert len(polynomial) == 9
    assert (135, 20749, 137233) in {
        (family.base_t, family.target_t, family.expected_norm_squareclass)
        for family in polynomial
    }
    assert (154, 20831, 142) in {
        (family.base_t, family.target_t, family.expected_norm_squareclass)
        for family in polynomial
    }
