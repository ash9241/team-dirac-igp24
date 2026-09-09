import pytest

from routeA.recover_submission_service import (
    _newer_items,
    _observable_count,
    _unique_match,
)


def test_audit_matches_only_one_new_batch_with_the_expected_size():
    old = {"submissionId": "old"}
    new = {
        "submissionId": "new",
        "payload": {"queuedPolynomials": list(range(97))},
        "verifiedPolynomials": [{}, {}, {}],
    }
    newer = _newer_items([new, old], "old")
    assert _observable_count(new) == 100
    assert _unique_match(newer, 100) is new


def test_audit_refuses_multiple_new_submissions():
    newer = [
        {"submissionId": "new-2", "payload": {"queuedPolynomials": 100}},
        {"submissionId": "new-1", "payload": {"queuedPolynomials": 100}},
    ]
    with pytest.raises(RuntimeError, match="not unique"):
        _unique_match(newer, 100)


def test_audit_requires_last_known_submission_boundary():
    with pytest.raises(RuntimeError, match="absent"):
        _newer_items([{"submissionId": "new"}], "old")
