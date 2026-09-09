import pytest

from routeA.gap_degree_product_map import build_gap_script, parse_gap_output


def test_generic_product_script_builds_exact_12x2_action():
    script = build_gap_script(12, 2, [(301, 1)])
    assert "TransitiveGroup(12,301)" in script
    assert "TransitiveGroup(2,1)" in script
    assert "TransitiveIdentification(g24)" in script
    assert "QuoInt(k-1,2)" in script


def test_generic_product_parser_and_degree_validation():
    records = parse_gap_output("PRODUCT|6|16|4|5|12345|1920\n")
    assert records == [{
        "degree_a": 6,
        "group_a": 16,
        "degree_b": 4,
        "group_b": 5,
        "target_t": 12345,
        "group_order": 1920,
    }]
    with pytest.raises(ValueError, match="multiply to 24"):
        build_gap_script(6, 3, [(1, 1)])
