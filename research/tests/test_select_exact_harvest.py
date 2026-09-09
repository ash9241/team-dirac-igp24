import hashlib
import json
from pathlib import Path

import pytest

from routeA.ledger import Ledger, candidate_hash, payload_hash
from routeA.select_exact_harvest import select_exact_harvest


def _candidate(t, r, constant, disc, **updates):
    coefficients = [constant] + [0] * 23 + [1]
    line = ",".join(map(str, coefficients))
    row = {
        "candidate_hash": candidate_hash(line),
        "coefficients": line,
        "target_t": t,
        "target_r": r,
        "local_root_count": r,
        "local_irreducible": True,
        "exact_compatibility_proven": True,
        "submission_ready": True,
        "estimated_nfdisc_abs": disc,
    }
    row.update(updates)
    return row


def _database(path: Path):
    with Ledger(path) as ledger:
        ledger.record_target_snapshot(
            [
                {"t": 10, "r": 0, "team_count": 0, "baseline": False},
                {
                    "t": 11,
                    "r": 2,
                    "team_count": 1,
                    "baseline": False,
                    "minimum_disc_abs": 10**8,
                },
                {"t": 12, "r": 4, "team_count": 0, "baseline": True},
                {"t": 13, "r": 6, "team_count": 0, "baseline": False},
                {"t": 14, "r": 8, "team_count": 0, "baseline": False},
            ],
            snapshot_id="current",
            captured_at="2026-07-15T12:00:00+00:00",
        )
        # Authoritative ownership comes from accepted verification history.
        owned = _candidate(13, 6, 99, 100)
        key = ledger.upsert_candidate(owned)
        ledger.record_verification(
            key,
            "submission-owned",
            {"status": "accepted", "t": 13, "r": 6},
        )
        committed = _candidate(14, 8, 98, 100)
        committed_key = ledger.upsert_candidate(committed)
        ledger.persist_batch(
            "batch-committed",
            [committed_key],
            payload_hash([committed["coefficients"]]),
            dry_run=False,
        )


def _write(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_selector_filters_authoritative_state_and_keeps_lowest_discriminant(tmp_path):
    db = tmp_path / "control.sqlite3"
    _database(db)
    shard_a = tmp_path / "a.jsonl"
    shard_b = tmp_path / "b.jsonl"
    output = tmp_path / "harvest.jsonl"
    low = _candidate(10, 0, 2, 100)
    high = _candidate(10, 0, 3, 1000)
    raid = _candidate(11, 2, 4, 10**10)
    _write(shard_a, [high, raid, _candidate(12, 4, 5, 100), _candidate(99, 0, 6, 100)])
    _write(
        shard_b,
        [low, _candidate(13, 6, 7, 100), _candidate(14, 8, 98, 100)],
    )

    summary = select_exact_harvest([shard_a, shard_b], output, db_path=db)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert [(row["target_t"], row["target_r"]) for row in rows] == [(10, 0), (11, 2)]
    assert rows[0]["candidate_hash"] == low["candidate_hash"]
    assert summary["counts"]["rejected_baseline"] == 1
    assert summary["counts"]["rejected_non_live"] == 1
    assert summary["counts"]["rejected_owned"] == 1
    assert summary["counts"]["rejected_committed_candidate"] == 1
    assert summary["counts"]["superseded_by_lower_discriminant"] == 1
    assert summary["forecast"]["solo_pairs"] == 1
    assert summary["forecast"]["raid_pairs"] == 1
    assert summary["forecast"]["immediate_score_ceiling"] == 1.5
    assert summary["forecast"]["discriminant_adjusted_score"] == pytest.approx(1.4)
    assert output.with_suffix(".jsonl.report.json").exists()
    assert summary["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_selector_requires_explicit_exact_and_ready_proof(tmp_path):
    db = tmp_path / "control.sqlite3"
    _database(db)
    shard = tmp_path / "candidates.jsonl"
    output = tmp_path / "harvest.jsonl"
    missing_exact = _candidate(10, 0, 2, 100)
    missing_exact.pop("exact_compatibility_proven")
    not_ready = _candidate(10, 0, 3, 100, submission_ready=False)
    root_mismatch = _candidate(10, 0, 4, 100, local_root_count=2)
    _write(shard, [missing_exact, not_ready, root_mismatch])

    summary = select_exact_harvest([shard], output, db_path=db)
    assert output.read_text() == ""
    assert summary["counts"]["rejected_not_exact"] == 1
    assert summary["counts"]["rejected_not_submission_ready"] == 1
    assert summary["counts"]["rejected_root_mismatch"] == 1


def test_selector_fails_closed_on_exact_candidate_hash_mismatch(tmp_path):
    db = tmp_path / "control.sqlite3"
    _database(db)
    shard = tmp_path / "candidates.jsonl"
    row = _candidate(10, 0, 2, 100)
    row["candidate_hash"] = "0" * 64
    _write(shard, [row])
    with pytest.raises(ValueError, match="hash mismatch"):
        select_exact_harvest([shard], tmp_path / "out.jsonl", db_path=db)
