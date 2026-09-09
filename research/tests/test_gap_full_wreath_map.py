from routeA.gap_full_wreath_map import build_gap_script, parse_gap_output


def test_full_wreath_script_and_parser_cover_all_degree12_groups() -> None:
    script = build_gap_script()
    assert "Concatenation(flips" in script
    rows = parse_gap_output(
        f"WREATH|{base}|{18000 + base}|{4096 * base}"
        for base in range(1, 302)
    )
    assert len(rows) == 301
    assert rows[0]["base_t"] == 1
    assert rows[-1]["target_t"] == 18301
