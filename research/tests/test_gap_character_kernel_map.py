from routeA.gap_character_kernel_map import parse_gap_output


def test_parse_gap_output_collapses_isomorphic_characters() -> None:
    rows = parse_gap_output(
        [
            "CHAR|1|101|0,0|true|false",
            "CHAR|1|102|1,1|false|true",
            "CHAR|2|201|0|true|true",
        ]
        + [f"CHAR|{base}|{1000 + base}|0|true|true" for base in range(3, 302)]
    )
    assert len(rows) == 302
    assert rows[0] == {
        "base_t": 1,
        "target_t": 101,
        "character_bits": [[False, False]],
        "includes_trivial_character": True,
        "includes_permutation_sign": False,
    }
    assert rows[1]["includes_permutation_sign"] is True
