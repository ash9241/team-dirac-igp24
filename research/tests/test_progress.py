import json

from routeA.progress import load_baseline_pairs, normalize_progress


def test_baseline_is_derived_from_best_file(tmp_path):
    path = tmp_path / "baseline_best.json"
    path.write_text(json.dumps({"1,0": "123", "7,24": "456"}))
    assert load_baseline_pairs(path) == {(1, 0), (7, 24)}


def test_progress_preserves_team_counts_and_excludes_baseline():
    labels = [{
        "label": "24T7",
        "t": 7,
        "signatures": [
            {"r": 0, "teamCount": 0, "discovered": False},
            {"r": 24, "teamCount": 1, "discovered": True, "minimumDiscAbs": "123456789"},
        ],
    }]
    normalized, pairs = normalize_progress(labels, {(7, 24)})
    assert normalized[0]["signatures"][1]["teamCount"] == 1
    assert pairs[0]["immediate_value"] == 1.0
    assert pairs[1]["baseline"] is True
    assert pairs[1]["immediate_value"] == 0.5
    assert pairs[1]["minimum_disc_abs"] == "123456789"
