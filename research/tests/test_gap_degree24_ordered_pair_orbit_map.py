import pytest

from routeA.gap_degree24_ordered_pair_orbit_map import (
    build_gap_script,
    parse_ordered_pair_orbits,
)


def test_parse_ordered_pair_orbits_marks_unambiguous_rows() -> None:
    rows = parse_ordered_pair_orbits([
        "noise",
        "ORDERED24|10|10,10,",
        "ORDERED24|11|11,12,",
    ])
    assert rows[0]["orbit_count"] == 2
    assert rows[0]["unambiguous_target_t"] == 10
    assert rows[1]["unambiguous_target_t"] is None


def test_ordered_pair_script_uses_distinct_tuples() -> None:
    script = build_gap_script(source_ts=[9, 2, 9])
    assert "Arrangements([1..24],2)" in script
    assert "OnTuples" in script
    assert "for t in [2,9]" in script
    with pytest.raises(ValueError, match="range"):
        build_gap_script(0, 9)
