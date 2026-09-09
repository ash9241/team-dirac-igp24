import sqlite3

import pytest

from routeA.ledger import Ledger, candidate_hash, canonical_coefficients, payload_hash


def polynomial(constant=1):
    return ",".join(map(str, [constant] + [0] * 23 + [1]))


def test_canonical_hash_and_validation():
    line = polynomial(7)
    assert canonical_coefficients(line) == line
    assert len(candidate_hash(line)) == 64
    with pytest.raises(ValueError):
        canonical_coefficients("1,2,1")
    with pytest.raises(ValueError):
        canonical_coefficients([1] + [0] * 24)


def test_batch_is_persisted_before_submission(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        line = polynomial(3)
        key = ledger.upsert_candidate({"coefficients": line})
        ledger.persist_batch("batch-1", [key], payload_hash([line]))
        row = ledger.batch("batch-1")
        assert row["status"] == "planned"
        assert ledger.batch_items("batch-1")[0]["candidate_hash"] == key
        ledger.mark_ambiguous("batch-1", "timeout")
        assert ledger.batch("batch-1")["status"] == "ambiguous"


def test_payload_cannot_be_reused_under_another_batch_uuid(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        line = polynomial(5)
        key = ledger.upsert_candidate({"coefficients": line})
        digest = payload_hash([line])
        ledger.persist_batch("batch-1", [key], digest)
        with pytest.raises(sqlite3.IntegrityError):
            ledger.persist_batch("batch-2", [key], digest)


def test_target_snapshot_persists_large_minimum_discriminant(tmp_path):
    huge = "9" * 200
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        ledger.record_target_snapshot([{
            "t": 42,
            "r": 8,
            "team_count": 1,
            "baseline": False,
            "minimum_disc_abs": huge,
        }])
        assert ledger.latest_targets()[(42, 8)]["minimum_disc_abs"] == huge


def test_verification_persists_server_field_discriminant(tmp_path):
    huge = str(10**100 + 9)
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        key = ledger.upsert_candidate({"coefficients": polynomial()})
        ledger.record_verification(key, "sub-test", {
            "status": "accepted",
            "t": 10,
            "r": 4,
            "fieldDiscAbs": huge,
        })
        row = ledger.connection.execute(
            "SELECT nfdisc FROM verification WHERE candidate_hash=?", (key,)
        ).fetchone()
        assert row["nfdisc"] == huge


def test_payloadless_server_history_is_authoritative_for_owned_pairs(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        first = ledger.observe_server_submission({
            "submissionId": "sub-history",
            "createdAt": "2026-07-01T00:00:00Z",
            "payload": {},
            "verifiedPolynomials": [{
                "polynomialIndex": 17,
                "status": "accepted",
                "t": 1234,
                "r": 8,
                "scoreable": True,
                "fieldDiscAbs": "12345678901234567890",
            }],
        })
        second = ledger.observe_server_submission({
            "submissionId": "sub-history",
            "payload": {},
            "verifiedPolynomials": [{
                "polynomialIndex": 17,
                "status": "accepted",
                "t": 1234,
                "r": 8,
                "scoreable": True,
            }],
        })
        assert first["new_results"] == 1
        assert second["new_results"] == 0
        assert ledger.verified_pairs() == set()
        assert ledger.owned_pairs() == {(1234, 8)}
        assert ledger.ownership_summary()["server_verifications"] == 1
