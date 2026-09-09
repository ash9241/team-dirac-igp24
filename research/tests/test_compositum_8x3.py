import shutil

import pytest

from routeA.constructions.compositum_8x3 import (
    classify_component,
    disjoint_ramification,
    generate_compositum,
)
from routeA.gap_product_map import build_gap_script
from routeA.gap_product_map import build_c2_fiber_gap_script
from routeA.gap_features import build_cycle_index_script, labels_from_census, parse_cycle_index
from routeA.constructions.fiber_product_8x3 import (
    generate_fiber_compositum,
    shares_sign_resolvent,
)
from routeA.constructions.relative_quadratic import generate_relative_quadratic
from routeA.constructions.quadratic_tower_cubic import generate_quadratic_tower


pytestmark = pytest.mark.skipif(not shutil.which("gp"), reason="PARI/GP not installed")


def test_component_classification_and_compositum_generation():
    octic = classify_component([-1, -1, 0, 0, 0, 0, 0, 0, 1])
    cubic = classify_component([-1, -1, 0, 1])
    assert octic.group_label == "8T50"
    assert cubic.group_label == "3T2"
    assert disjoint_ramification(octic, cubic)
    candidate = generate_compositum(
        octic, cubic, primitive_parameter=1, target_t=12345
    )
    assert candidate.target_r == octic.root_count * cubic.root_count
    assert len(candidate.coefficients.split(",")) == 25
    assert candidate.discriminant_disjoint
    assert candidate.estimated_nfdisc_abs == (
        abs(octic.discriminant) ** 3 * abs(cubic.discriminant) ** 8
    )
    assert octic.discriminant_squareclass is not None


def test_gap_script_uses_exact_product_action_identification():
    script = build_gap_script([(50, 2)])
    assert "TransitiveIdentification(g24)" in script
    assert "TransitiveGroup(8,50)" in script
    assert "TransitiveGroup(3,2)" in script
    fiber = build_c2_fiber_gap_script([(50, 2)])
    assert "SignPerm" in fiber
    assert "GroupHomomorphismByFunction" in fiber
    assert "function(p)" in fiber
    assert "FIBER_UNAVAILABLE|50|2|" in fiber
    assert "FIBER|50|2|" in fiber


def test_live_17920_fiber_seed_has_exact_signature_and_resolvent():
    octic = classify_component([1, -2, -4, 4, 7, -1, -5, 0, 1])
    cubic = classify_component([1988253, 29844, 0, 1])
    assert (octic.group_label, octic.root_count) == ("8T50", 6)
    assert (cubic.group_label, cubic.root_count) == ("3T2", 1)
    assert octic.discriminant_squareclass == -65106259
    assert shares_sign_resolvent(octic, cubic)
    candidate = generate_fiber_compositum(
        octic, cubic, primitive_parameter=1, target_t=17920
    )
    assert candidate.target_r == 6
    assert candidate.local_irreducible
    assert candidate.estimated_nfdisc_abs is None
    assert len(candidate.coefficients.split(",")) == 25


def test_cycle_index_parser_normalizes_class_sizes():
    parsed = parse_cycle_index("INDEX|7|1.1.2|2|4\nINDEX|7|4|2|4\n")
    assert parsed == {7: {"1.1.2": 0.5, "4": 0.5}}
    script = build_cycle_index_script([7])
    assert "ConjugacyClasses(g)" in script
    assert "TransitiveGroup(24,7)" in script
    assert "TransitiveGroup(6,7)" in build_cycle_index_script([7], degree=6)


def test_cycle_index_census_shape_filter(tmp_path):
    path = tmp_path / "census.jsonl"
    path.write_text(
        '{"t":10,"block_sizes":[2,4,8]}\n'
        '{"t":11,"block_sizes":[3,6,12]}\n'
    )
    assert labels_from_census(path, [8, 2, 4]) == [10]


def test_relative_quadratic_and_nested_tower_generators():
    relative = generate_relative_quadratic(
        [-2] + [0] * 11 + [1],
        [3, 1],
        target_t=1,
        expected_root_count=4,
    )
    assert len(relative.coefficients.split(",")) == 25
    tower = generate_quadratic_tower(
        [-1, -1, 0, 1],
        {
            "a1": [2, 1],
            "a2_A": [3, 1],
            "a2_B": [1],
            "a3_A": [5, 1],
            "a3_B": [1],
            "a3_C": [1],
            "a3_D": [1],
        },
        target_t=2,
    )
    assert tower.local_root_count == 8
    assert len(tower.coefficients.split(",")) == 25
    assert len(tower.intermediate_degree6_coefficients) == 7
    assert len(tower.intermediate_degree12_coefficients) == 13
