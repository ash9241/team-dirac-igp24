import pytest

from routeA.oracle_v2 import CycleIndexOracle
from routeA.structural_oracle import (
    annotate_predictions,
    learn_family_profiles,
    normalize_family,
    open_target_values,
)


def polynomial(constant=1):
    return ",".join(map(str, [constant] + [0] * 23 + [1]))


def test_structural_oracle_only_emits_open_high_probability_pair():
    oracle = CycleIndexOracle({
        10: {"1.1.2.4.16": 0.9, "24": 0.1},
        11: {"1.1.2.4.16": 0.1, "24": 0.9},
    })
    candidates = [{
        "coefficients": polynomial(),
        "local_root_count": 4,
        "recipe_family": "test",
        "recipe_lineage": "lineage",
    }]
    fingerprints = [{"1, 1, 2, 4, 16": 1.0}]
    annotated = annotate_predictions(
        candidates,
        fingerprints,
        oracle,
        {(10, 4): 1.0},
        min_probability=0.5,
        family_profiles=learn_family_profiles(
            [{"t": 10, "dial": {"cls": "test"}} for _ in range(20)]
        ),
    )
    assert len(annotated) == 1
    assert annotated[0]["target_t"] == 10
    assert annotated[0]["target_r"] == 4


def test_open_values_exclude_owned_and_baseline():
    progress = [{"t": 10, "signatures": [
        {"r": 4, "teamCount": 0, "baseline": False},
        {"r": 8, "teamCount": 1, "baseline": False},
        {"r": 12, "teamCount": 0, "baseline": True},
    ]}]
    assert open_target_values(progress, {(10, 8)}) == {(10, 4): 1.0}


def test_family_profile_blocks_cycle_compatible_but_unseen_label():
    oracle = CycleIndexOracle({
        10: {"1.1.2.4.16": 0.9, "24": 0.1},
        11: {"1.1.2.4.16": 0.1, "24": 0.9},
    })
    profiles = learn_family_profiles([
        {"t": 11, "dial": {"cls": "known"}}
        for _ in range(20)
    ])
    candidates = [{
        "coefficients": polynomial(),
        "local_root_count": 4,
        "recipe_family": "known",
        "recipe_lineage": "lineage",
    }]
    annotated = annotate_predictions(
        candidates,
        [{"1, 1, 2, 4, 16": 1.0}],
        oracle,
        {(10, 4): 1.0},
        family_profiles=profiles,
    )
    assert annotated == []


def test_unknown_family_requires_explicit_compatible_labels():
    oracle = CycleIndexOracle({10: {"24": 1.0}})
    candidate = {
        "coefficients": polynomial(3),
        "local_root_count": 4,
        "recipe_family": "new-family",
        "recipe_lineage": "lineage",
    }
    assert annotate_predictions(
        [candidate], [{"24": 1.0}], oracle, {(10, 4): 1.0}
    ) == []
    candidate["compatible_labels"] = [10]
    annotated = annotate_predictions(
        [candidate], [{"24": 1.0}], oracle, {(10, 4): 1.0}
    )
    assert annotated[0]["target_t"] == 10
    assert not annotated[0]["submission_ready"]
    assert annotated[0]["calibration_status"] == "needs_prospective_calibration"

    candidate["exact_compatibility_proven"] = True
    proven = annotate_predictions(
        [candidate], [{"24": 1.0}], oracle, {(10, 4): 1.0}
    )
    assert proven[0]["submission_ready"]


def test_family_profile_supplies_prior_and_audit_metadata():
    oracle = CycleIndexOracle({10: {"24": 1.0}, 11: {"24": 1.0}})
    profiles = learn_family_profiles(
        [{"t": 10, "dial": {"cls": "known"}} for _ in range(18)]
        + [{"t": 11, "dial": {"cls": "known"}} for _ in range(2)]
    )
    annotated = annotate_predictions(
        [{
            "coefficients": polynomial(2),
            "local_root_count": 4,
            "recipe_family": "empirical_harvest_known",
            "recipe_lineage": "lineage",
        }],
        [{"24": 1.0}],
        oracle,
        {(10, 4): 1.0},
        family_profiles=profiles,
    )
    assert annotated[0]["target_t"] == 10
    assert annotated[0]["label_probability"] == pytest.approx(0.9)
    assert annotated[0]["family_profile_support"] == 20
    assert annotated[0]["submission_ready"]
    assert normalize_family("empirical_harvest_known") == "known"
