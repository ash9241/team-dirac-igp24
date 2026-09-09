from routeA.select_exact_harvest import LiveTarget
from cloud.prioritize_signature_action_matrix import (
    forecast_task_signatures,
    prioritize_tasks,
)


def _target(r, team_count=0, baseline=False):
    return LiveTarget(
        t=20,
        r=r,
        team_count=team_count,
        baseline=baseline,
        minimum_disc_abs=None,
        snapshot_id="current",
        captured_at="2026-07-20T00:00:00+00:00",
    )


def _profile():
    return {
        "source_t": 10,
        "orbit_count": 2,
        "orbit_targets": [20, 20],
        "profiles": [
            {
                "class_size": 3,
                "source_cycle": [1] * 8 + [2] * 8,
                "orbit_cycles": [
                    [1] * 4 + [2] * 10,
                    [1] * 8 + [2] * 8,
                ],
            },
            {
                "class_size": 1,
                "source_cycle": [1] * 8 + [2] * 8,
                "orbit_cycles": [
                    [1] * 12 + [2] * 6,
                    [1] * 12 + [2] * 6,
                ],
            },
        ],
    }


def test_forecast_conditions_on_source_involution_and_deduplicates_pairs():
    targets = {
        (20, 4): _target(4),
        (20, 8): _target(8, team_count=1),
        (20, 12): _target(12),
    }
    forecast = forecast_task_signatures(
        source_r=8,
        target_t=20,
        profile=_profile(),
        targets=targets,
        owned_pairs={(20, 4)},
    )
    # Mass-three class reaches only the r=8 raid; the mass-one class reaches
    # one deduplicated r=12 pair even though both abstract orbits have r=12.
    assert forecast.expected_score == (3 * 0.5 + 1 * 1.0) / 4
    assert forecast.maximum_score == 1.0
    assert forecast.minimum_score == 0.5
    assert forecast.possible_root_sets == ((4, 8), (12,))


def test_prioritizer_rejects_zero_opportunity_and_orders_expected_value():
    targets = {
        (20, 4): _target(4),
        (20, 8): _target(8, team_count=1),
        (20, 12): _target(12),
    }
    tasks = [
        {"job_id": "low", "source_t": 10, "target_t": 20},
        {"job_id": "done", "source_t": 10, "target_t": 20},
    ]
    sources = {
        "low": {"source_t": 10, "source_r": 8},
        "done": {"source_t": 10, "source_r": 8},
    }
    ranked, counts = prioritize_tasks(
        tasks,
        {10: _profile()},
        targets,
        {(20, 4), (20, 8)},
        lambda task: sources[task["job_id"]],
        excluded_job_ids={"done"},
    )
    assert [row["job_id"] for row in ranked] == ["low"]
    assert ranked[0]["signature_expected_score"] == 0.25
    assert counts["excluded_completed_tasks"] == 1
