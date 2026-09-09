from routeA.scheduler import (
    CandidateOption,
    PortfolioScheduler,
    TargetState,
    discriminant_score_ratio,
    effective_sample_size,
    candidate_submission_ready,
)
from routeA.extension_matcher import build_queue
from routeA.metrics import replacement_ready, wilson_lower_bound
from routeA.oracle_v2 import CycleIndexOracle, normalize_cycle_pattern


def polynomial(constant):
    return ",".join(map(str, [constant] + [0] * 23 + [1]))


def option(constant, pair=(10, 4), lineage="a", probability=0.8):
    return CandidateOption(
        coefficients=polynomial(constant),
        target_t=pair[0],
        target_r=pair[1],
        label_probability=probability,
        recipe_family="test",
        recipe_lineage=lineage,
        local_root_count=pair[1],
    )


def test_scheduler_enforces_independent_lineages_and_target_cap():
    candidates = [
        option(1, lineage="same"),
        option(2, lineage="same"),
        option(3, lineage="other"),
        option(4, lineage="third"),
    ]
    targets = {(10, 4): TargetState(10, 4, 0)}
    chosen = PortfolioScheduler(max_per_target=2).select(candidates, targets, 10)
    assert len(chosen) == 2
    assert len({row.candidate.recipe_lineage for row in chosen}) == 2


def test_scheduler_prefers_gold_to_equal_probability_raid():
    candidates = [option(1, (10, 4), "a"), option(2, (11, 4), "b")]
    targets = {(10, 4): TargetState(10, 4, 0), (11, 4): TargetState(11, 4, 1)}
    ranked = PortfolioScheduler().rank(candidates, targets)
    assert ranked[0].target.team_count == 0


def test_scheduler_prefers_lower_discriminant_for_same_raid():
    candidates = [
        CandidateOption(
            **{
                **option(1, (11, 4), "large").__dict__,
                "estimated_disc_abs": 10**100,
            }
        ),
        CandidateOption(
            **{
                **option(2, (12, 4), "small").__dict__,
                "estimated_disc_abs": 10**20,
            }
        ),
    ]
    targets = {
        (11, 4): TargetState(11, 4, 1, minimum_disc_abs=10**10),
        (12, 4): TargetState(12, 4, 1, minimum_disc_abs=10**10),
    }
    ranked = PortfolioScheduler().rank(candidates, targets)
    assert ranked[0].candidate.target_t == 12
    assert ranked[0].discriminant_ratio > ranked[1].discriminant_ratio
    assert discriminant_score_ratio(10**10, 10**20) == 0.5


def test_effective_sample_size_accounts_for_correlation():
    assert effective_sample_size(4, 0) == 4
    assert effective_sample_size(4, 1) == 1


def test_extension_matcher_rejects_coarse_proxy_evidence():
    queue = build_queue(
        {(10, 4), (11, 8)},
        [
            {"recipe_id": "bad", "compatibility": "same-order", "compatible_targets": [10]},
            {
                "recipe_id": "good",
                "family": "8x3",
                "compatibility": "gap-exact",
                "compatible_targets": [11],
                "recipe": {"fam": "8x3"},
            },
        ],
    )
    assert "10" not in queue
    assert queue["11"]["matcher_version"] == "extension-v1"


def test_statistical_replacement_gates():
    assert 0 < wilson_lower_bound(85, 100) < 0.85
    ready, lower = replacement_ready({"a": [1.0, 1.0], "b": [0.8, 0.8]}, 0.1)
    assert ready
    assert lower > 0.2


def test_cycle_index_oracle_normalizes_legacy_pattern_format():
    assert normalize_cycle_pattern("[1, 1, 2, 4]") == "1.1.2.4"
    oracle = CycleIndexOracle({10: {"1.1.2.4": 1.0}})
    prediction = oracle.predict({"1, 1, 2, 4": 5}, [10])
    assert prediction.probabilities == {10: 1.0}


def test_scheduler_rejects_explicitly_uncalibrated_candidate():
    candidate = CandidateOption.from_mapping({
        "coefficients": polynomial(7),
        "target_t": 10,
        "target_r": 4,
        "local_root_count": 4,
        "label_probability": 0.999,
        "family_profile_support": 0,
    })
    assert not candidate.submission_ready
    assert not candidate_submission_ready(candidate.metadata)
    assert PortfolioScheduler().rank(
        [candidate], {(10, 4): TargetState(10, 4, 0)}
    ) == []
