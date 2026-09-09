import pytest

from routeA.gap_degree24_pair_orbit_map import (
    build_gap_script,
    parse_pair_orbits,
)


def test_parse_pair_orbits_marks_only_single_target_rows_unambiguous() -> None:
    rows = parse_pair_orbits([
        "noise",
        "PAIR24|10|20",
        "PAIR24|10|20",
        "PAIR24|11|21",
        "PAIR24|11|22",
    ])
    assert rows[0]["source_t"] == 10
    assert rows[0]["orbit_count"] == 2
    assert rows[0]["unambiguous_target_t"] == 20
    assert rows[1]["unique_targets"] == [21, 22]
    assert rows[1]["unambiguous_target_t"] is None


def test_pair_orbit_script_validates_transitive_group_range() -> None:
    assert "TransitiveGroup(24,t)" in build_gap_script(7, 9)
    with pytest.raises(ValueError, match="range"):
        build_gap_script(0, 9)


def test_pair_orbit_script_can_scan_only_verified_sources() -> None:
    script = build_gap_script(source_ts=[9, 2, 9])
    assert "for t in [2,9]" in script
    assert "[1..25000]" not in script
    assert 'Print("PAIR24|",t,"|"' in script
