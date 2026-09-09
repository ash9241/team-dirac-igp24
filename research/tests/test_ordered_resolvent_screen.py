from unittest.mock import patch

from routeA.ordered_resolvent_screen import refine


def test_refine_propagates_degree12_support_to_degree24_labels():
    candidates = [{
        "candidate_hash": "abc",
        "compatible_labels": [10, 11],
        "quotient_evidence": {
            "quotient3": 1,
            "possible_quotient6": [7],
            "possible_quotient12": [50, 51],
        },
    }]
    atlas = {
        50: {"2.2": 1.0},
        51: {"3.1": 1.0},
    }
    census = [
        {
            "t": label,
            "block_sizes": [2, 4, 8],
            "block_quotients": [
                {"block_size": 8, "quotient_degree": 3, "quotient_t": 1},
                {"block_size": 4, "quotient_degree": 6, "quotient_t": 7},
                {"block_size": 2, "quotient_degree": 12, "quotient_t": quotient},
            ],
        }
        for label, quotient in ((10, 50), (11, 51))
    ]
    with patch(
        "routeA.ordered_resolvent_screen.unramified_cycle_patterns",
        return_value=[{"2.2"}],
    ):
        rows = refine(candidates, ["1,0,1"], atlas, census)
    assert rows[0]["compatible_labels"] == [10]
    assert rows[0]["quotient_evidence"]["possible_quotient12"] == [50]
