import pytest

from routeA.gap_degree24_pair_joint_profiles import (
    build_gap_script,
    parse_joint_profiles,
)


def test_joint_profile_script_couples_source_and_pair_actions() -> None:
    script = build_gap_script([1007, 164, 164])
    assert "for t in [164,1007]" in script
    assert "ActionHomomorphism(G,o,OnSets)" in script
    assert 'Print("JCLASS|"' in script


def test_joint_profile_script_supports_ordered_action() -> None:
    script = build_gap_script([164], action_kind="ordered-pair")
    assert "Arrangements([1..24],2)" in script
    assert "ActionHomomorphism(G,o,OnTuples)" in script


def test_joint_profile_script_requires_valid_source() -> None:
    with pytest.raises(ValueError, match="at least one"):
        build_gap_script([])
    with pytest.raises(ValueError, match=r"\[1, 25000\]"):
        build_gap_script([25_001])


def test_parse_joint_profiles_aggregates_observationally_equal_classes() -> None:
    rows = parse_joint_profiles([
        "noise",
        "JGROUP|164|12|163,164",
        "JCLASS|164|4|24|12.12;8.8.8",
        "JCLASS|164|2|12.12|6.6.6.6;4.4.4.4.4.4",
        "JCLASS|164|6|24|12.12;8.8.8",
    ])
    assert len(rows) == 1
    row = rows[0]
    assert row["orbit_targets"] == [163, 164]
    assert row["profile_count"] == 2
    assert sum(item["class_size"] for item in row["profiles"]) == 12
    assert max(item["class_size"] for item in row["profiles"]) == 10


def test_parse_joint_profiles_rejects_incomplete_class_census() -> None:
    with pytest.raises(ValueError, match="sum to"):
        parse_joint_profiles([
            "JGROUP|164|12|163,164",
            "JCLASS|164|4|24|12.12;8.8.8",
        ])
