from routeA.family_calibration import evaluate_family_calibration


def observation(predicted, verified, lineage, compatible=(10, 11, 12)):
    return {
        "accepted": True,
        "selected_t": predicted,
        "selected_r": 20,
        "verified_t": verified,
        "verified_r": 20,
        "lineage": lineage,
        "compatible_labels": list(compatible),
    }


def test_family_calibration_blocks_d4_style_failure():
    report = evaluate_family_calibration(
        "d4",
        [
            observation(10, 10, "same"),
            observation(11, 10, "same"),
            observation(12, 10, "same"),
            observation(12, 13, "same"),
        ],
    )
    assert not report.passed
    assert report.accepted_trials == 4
    assert report.exact_label_hits == 1
    assert report.exact_label_precision == 0.25
    assert report.independent_lineages == 1
    assert report.compatibility_violations == {"13": 1}


def test_family_calibration_passes_supported_independent_results():
    report = evaluate_family_calibration(
        "proven",
        [
            observation(10, 10, "a"),
            observation(10, 10, "b"),
            observation(11, 11, "c"),
            observation(11, 11, "d"),
            observation(12, 12, "e"),
        ],
    )
    assert report.passed
    assert report.exact_label_precision == 1.0
    assert report.independent_lineages == 5
    assert report.compatibility_violations == {}
