import json

from routeA.gap_block_census import (
    all_target_ids,
    build_gap_script,
    live_target_ids,
    parse_gap_output,
)


def test_live_target_selection_excludes_baseline_and_crowded_pairs(tmp_path):
    progress = tmp_path / "progress.json"
    progress.write_text(json.dumps([
        {"t": 1, "signatures": [{"r": 0, "teamCount": 0, "baseline": True}]},
        {"t": 2, "signatures": [{"r": 4, "teamCount": 1, "baseline": False}]},
        {"t": 3, "signatures": [{"r": 8, "teamCount": 2, "baseline": False}]},
    ]))
    assert live_target_ids(progress) == [2]
    assert all_target_ids(progress) == [1, 2, 3]


def test_block_census_script_and_parser():
    script = build_gap_script([17920])
    assert "AllBlocks(g)" in script
    assert "TransitiveIdentification(act)" in script
    assert "TransitiveGroup(24,17920)" in script
    records = parse_gap_output(
        "GROUP|17920|120960|false|false\n"
        "BLOCK|17920|8|3|2\n"
        "BLOCK|17920|8|3|2\n"
        "BLOCK|17920|3|8|50\n"
    )
    assert records == [{
        "t": 17920,
        "order": "120960",
        "primitive": False,
        "solvable": False,
        "block_sizes": [3, 8],
        "block_quotients": [
            {"block_size": 3, "quotient_degree": 8, "quotient_t": 50},
            {"block_size": 8, "quotient_degree": 3, "quotient_t": 2},
        ],
        "evidence": "gap-exact",
    }]
