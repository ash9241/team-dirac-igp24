from routeA.constructions.integral_basis_character_lift import (
    build_integral_basis_lift,
    enumerate_integral_basis_generators,
    family_from_integral_basis_seed,
)


BASE_12T156 = (
    13520, -40560, -30420, 94900, 71565, -21060, -19381,
    1674, 1926, -46, -78, 0, 1,
)

NORM_605_SEED = (
    "-7657403003885/41826499577",
    "42180280659303/334611996616",
    "437217933910199/669223993232",
    "5295848071876773/17399823824032",
    "-1332739749153159/8699911912016",
    "-1622131649236473/17399823824032",
    "110971888510643/8699911912016",
    "82531015405725/8699911912016",
    "-3409000996833/8699911912016",
    "-3345954311243/8699911912016",
    "17464573937/8699911912016",
    "85267775449/17399823824032",
)


def test_integral_basis_path_avoids_artificial_denominator_ramification() -> None:
    family = family_from_integral_basis_seed(
        family="test-12t156-norm5",
        base_t=156,
        target_t=21372,
        expected_norm_squareclass=5,
        base_coefficients=BASE_12T156,
        seed_coefficients=NORM_605_SEED,
        target_root_counts=(12,),
    )
    option = enumerate_integral_basis_generators(
        family,
        options_per_signature=1,
    )[12][0]
    spec = build_integral_basis_lift(family, option)

    assert spec.target_r == 12
    assert len(spec.coefficients) == 25
    assert spec.coefficients[-1] == 1
    assert max(abs(value) for value in spec.coefficients) < 10**12
