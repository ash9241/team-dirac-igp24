import pytest

from routeA.ledger import candidate_hash
from routeA.select_discriminant_improvements import select_improvement_rows
from routeA.select_exact_harvest import LiveTarget


def _candidate(constant, disc, **updates):
    coefficients = [constant] + [0] * 23 + [1]
    line = ",".join(map(str, coefficients))
    row = {
        "candidate_hash": candidate_hash(line),
        "coefficients": line,
        "target_t": 10,
        "target_r": 4,
        "local_root_count": 4,
        "local_irreducible": True,
        "exact_compatibility_proven": True,
        "submission_ready": True,
        "estimated_nfdisc_abs": disc,
    }
    row.update(updates)
    return row


def _target():
    return LiveTarget(
        t=10,
        r=4,
        team_count=2,
        baseline=False,
        minimum_disc_abs=10**8,
        snapshot_id="current",
        captured_at="2026-07-20T00:00:00+00:00",
    )


def test_improvement_selector_uses_existing_holder_ceiling_and_best_disc():
    rows, counts = select_improvement_rows(
        [_candidate(2, 10**10), _candidate(3, 10**9), _candidate(4, 10**13)],
        {(10, 4): _target()},
        {(10, 4): 10**12},
    )
    assert len(rows) == 1
    assert rows[0]["estimated_nfdisc_abs"] == 10**9
    detail = rows[0]["discriminant_improvement"]
    assert detail["score_ceiling"] == 0.5
    assert detail["current_ratio"] == pytest.approx(2 / 3)
    assert detail["candidate_ratio"] == pytest.approx(8 / 9)
    assert detail["forecast_score_gain"] == pytest.approx(1 / 9)
    assert counts["rejected_not_lower_discriminant"] == 1
    assert counts["superseded_by_lower_discriminant"] == 1


def test_improvement_selector_rejects_committed_and_hash_mismatch():
    committed = _candidate(2, 10**9)
    rows, counts = select_improvement_rows(
        [committed],
        {(10, 4): _target()},
        {(10, 4): 10**12},
        {committed["candidate_hash"]},
    )
    assert rows == []
    assert counts["rejected_committed_candidate"] == 1

    bad = _candidate(3, 10**9, candidate_hash="0" * 64)
    with pytest.raises(ValueError, match="hash mismatch"):
        select_improvement_rows(
            [bad], {(10, 4): _target()}, {(10, 4): 10**12}
        )
