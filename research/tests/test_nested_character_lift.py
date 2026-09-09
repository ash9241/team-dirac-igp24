import pytest
import shutil
from pathlib import Path

from routeA.constructions.nested_character_lift import (
    DEFAULT_GAP,
    _target_cycle_data,
    build_nested_character_lift,
    classify_transitive_subgroup,
)


def test_nested_character_lift_builds_exact_integral_resultant() -> None:
    spec = build_nested_character_lift(
        family="test",
        base_t=249,
        target_t=23312,
        q_coefficients=(-1, -12, 0, 1),
        e=105,
        d=7200,
        expected_norm_squareclass=2,
        h_linear_roots=(4,),
        h_positive_quadratics=((-14, 24),),
    )
    assert len(spec.base_coefficients) == 13
    assert len(spec.coefficients) == 25
    assert spec.coefficients[-1] == 1
    assert spec.h_coefficients == (-3088, 660, 24, 1)
    assert spec.parameters["norm_squareclass"] == 2


def test_nested_character_lift_rejects_nonpositive_radius() -> None:
    with pytest.raises(ValueError, match="radii"):
        build_nested_character_lift(
            family="test",
            base_t=249,
            target_t=23312,
            q_coefficients=(-1, -12, 0, 1),
            e=105,
            d=7200,
            expected_norm_squareclass=2,
            h_linear_roots=(4,),
            h_positive_quadratics=((0, 0),),
        )


@pytest.mark.skipif(not (shutil.which(DEFAULT_GAP) or Path(DEFAULT_GAP).is_file()), reason="Requires GAP and the degree-24 transitive-group catalog")
def test_transitive_subgroup_descent_recovers_proper_kummer_group() -> None:
    # A sign-character lift over 12T73.  Its Kummer module is proper in
    # 24T19178 and descends uniquely to 24T1289.
    coefficients = (
        278629, -118937, 1465555, 110367, 2624872, 1569297, 2760308,
        1612173, 1362817, 1047788, 750193, 459356, 280517, 149329,
        77001, 32069, 13180, 4517, 1232, 219, 64, -11, -3, -2, 1,
    )
    target_t, excluded, witnesses, chain = classify_transitive_subgroup(
        coefficients,
        19178,
    )
    assert target_t == 1289
    assert chain[0] == 19178
    assert chain[-1] == 1289
    assert excluded
    assert witnesses


@pytest.mark.skipif(not (shutil.which(DEFAULT_GAP) or Path(DEFAULT_GAP).is_file()), reason="Requires GAP and the degree-24 transitive-group catalog")
def test_transitive_terminal_without_proper_transitive_maximals_is_valid() -> None:
    cycle_types, maximal_types = _target_cycle_data(506, DEFAULT_GAP, 1200)
    assert cycle_types
    assert maximal_types == {}
