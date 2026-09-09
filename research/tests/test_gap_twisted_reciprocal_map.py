from routeA.gap_twisted_reciprocal_map import (
    CHARACTERS,
    build_gap_script,
    parse_gap_output,
)


def test_gap_script_contains_all_four_characters() -> None:
    script = build_gap_script()
    assert "TransitiveIdentification(B)" in script
    for name, bits in CHARACTERS.items():
        assert f'["{name}",[{",".join(map(str, bits))}]]' in script


def test_parse_gap_output_ignores_package_notices() -> None:
    output = """\
#I harmless package notice
BASE|299|1036800
TWIST|even|24870|2123366400|true
TWIST|top|24868|2123366400|false
TWIST|within|24871|2123366400|false
TWIST|product|24869|2123366400|false
"""
    result = parse_gap_output(output)
    assert result["base_t"] == 299
    assert result["base_order"] == 1_036_800
    assert [row["target_t"] for row in result["twists"]] == [24870, 24868, 24871, 24869]
