import pytest

from routeA.gap_degree24_subset_orbit_map import (
    build_gap_script,
    parse_subset_orbits,
)


def test_parse_subset_orbits_tracks_nonunique_targets() -> None:
    rows = parse_subset_orbits(
        [
            "noise",
            "SUBSET24|10|11",
            "SUBSET24|10|11",
            "SUBSET24|12|13",
            "SUBSET24|12|14",
        ],
        subset_size=4,
    )
    assert rows[0]["subset_size"] == 4
    assert rows[0]["unambiguous_target_t"] == 11
    assert rows[1]["unambiguous_target_t"] is None


def test_subset_script_uses_fixed_size_sets() -> None:
    script = build_gap_script(4, source_ts=[9, 2, 9])
    assert "Combinations([1..24],4)" in script
    assert "OnSets" in script
    assert "for t in [2,9]" in script
    with pytest.raises(ValueError, match="subset size"):
        build_gap_script(1)
