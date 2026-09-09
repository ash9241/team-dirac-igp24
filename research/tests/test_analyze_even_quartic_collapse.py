from routeA.analyze_even_quartic_collapse import analyze


def test_forensic_report_counts_expected_and_exceptional_labels():
    rows = [
        {"candidate_hash": "a", "parameters": {"form": "pure4"}},
        {"candidate_hash": "b", "parameters": {"form": "c4"}},
        {"candidate_hash": "c", "parameters": {"form": "v4-entangled"}},
        {"candidate_hash": "d", "parameters": {"form": "d4-k2"}},
        {"candidate_hash": "e", "parameters": {"form": "pure4"}},
    ]
    report = analyze(
        rows,
        {
            "a": (19036, 24),
            "b": (17757, 16),
            "c": (7181, 8),
            "d": (19036, 4),
            "e": (999, 0),
        },
    )
    assert report["matched_rows"] == 5
    assert report["regimes"]["D4_linked"]["predicted_matches"] == 2
    assert report["regimes"]["D4_linked"]["precision"] == 2 / 3
