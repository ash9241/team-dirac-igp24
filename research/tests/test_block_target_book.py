from routeA.block_target_book import build_target_book


def test_target_book_filters_shape_baseline_crowded_and_owned_pairs():
    progress = [{
        "t": 10,
        "signatures": [
            {"r": 4, "teamCount": 0, "baseline": False},
            {"r": 8, "teamCount": 1, "baseline": False},
            {"r": 12, "teamCount": 2, "baseline": False},
            {"r": 24, "teamCount": 0, "baseline": True},
        ],
    }]
    census = [{
        "t": 10,
        "order": "384",
        "solvable": True,
        "block_sizes": [2, 4, 8],
        "block_quotients": [],
    }]
    records = build_target_book(
        progress,
        census,
        block_shape=[2, 4, 8],
        owned_pairs={(10, 8)},
    )
    assert [(record["t"], record["r"]) for record in records] == [(10, 4)]
    assert records[0]["score_ceiling"] == 1.0
