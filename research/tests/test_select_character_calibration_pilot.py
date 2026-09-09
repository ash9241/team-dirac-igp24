import hashlib
import json
from pathlib import Path

import pytest

from routeA.ledger import Ledger, candidate_hash, payload_hash
from routeA.select_character_calibration_pilot import (
    select_character_calibration_pilot,
)


def _candidate(
    base_t: int,
    norm_squareclass: int,
    target_t: int,
    target_r: int,
    constant: int,
    disc: int,
    **updates,
):
    coefficients = [constant] + [0] * 23 + [1]
    line = ",".join(str(value) for value in coefficients)
    row = {
        "candidate_hash": candidate_hash(line),
        "coefficients": line,
        "target_t": target_t,
        "target_r": target_r,
        "local_root_count": target_r,
        "local_irreducible": True,
        "submission_ready": True,
        "exact_compatibility_proven": True,
        "valid_probability": 1.0,
        "label_probability": 1.0,
        "field_disc_abs": disc,
        "estimated_nfdisc_abs": disc,
        "norm_squareclass": norm_squareclass,
        "expected_norm_squareclass": norm_squareclass,
        "parameters": {
            "base_t": base_t,
            "norm_squareclass": norm_squareclass,
            "target_t": target_t,
        },
        "recipe_family": f"character-{base_t}-{norm_squareclass}-{target_t}",
    }
    row.update(updates)
    return row


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_selector_keeps_owned_pair_and_chooses_lowest_discriminant(tmp_path):
    high = _candidate(1, 5, 10, 4, 2, 1000)
    low_owned = _candidate(1, 5, 10, 4, 3, 100)
    other = _candidate(2, -7, 20, 0, 4, 200)
    source = tmp_path / "candidates.jsonl"
    _write_jsonl(source, [high, low_owned, other])

    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        # This unrelated polynomial owns (10, 4), but is not present in the
        # supplied files and therefore cannot calibrate the (1, 5, 10) key.
        owner = _candidate(99, 99, 99, 4, 99, 999)
        owner_hash = ledger.upsert_candidate(owner)
        ledger.record_verification(
            owner_hash,
            "owner-submission",
            {"status": "accepted", "t": 10, "r": 4},
        )

    output = tmp_path / "pilot.jsonl"
    summary = select_character_calibration_pilot([source], output, db_path=db)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert [row["candidate_hash"] for row in rows] == [
        low_owned["candidate_hash"],
        other["candidate_hash"],
    ]
    assert rows[0]["calibration_pilot"]["predicted_pair_already_owned"] is True
    assert summary["counts"]["selected_owned_predicted_pairs"] == 1
    assert summary["counts"]["selected_unowned_predicted_pairs"] == 1
    assert summary["counts"]["superseded_same_key"] == 1
    assert summary["counts"]["selected_calibration_keys"] == 2
    assert summary["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert output.with_suffix(".jsonl.report.json").exists()


def test_selector_excludes_key_calibrated_by_supplied_accepted_candidate(tmp_path):
    calibrated = _candidate(1, 5, 10, 0, 2, 100)
    fresh_same_key = _candidate(1, 5, 10, 4, 3, 90)
    fresh_key = _candidate(1, 7, 11, 0, 4, 80)
    candidates = tmp_path / "fresh.jsonl"
    history = tmp_path / "history.jsonl"
    _write_jsonl(candidates, [fresh_same_key, fresh_key])
    _write_jsonl(history, [calibrated])

    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        key = ledger.upsert_candidate(calibrated)
        # A different verified T still calibrates the predicted key.
        ledger.record_verification(
            key,
            "calibration-submission",
            {"status": "accepted", "t": 12, "r": 0},
        )

    output = tmp_path / "pilot.jsonl"
    summary = select_character_calibration_pilot(
        [candidates, history],
        output,
        db_path=db,
    )
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert [row["candidate_hash"] for row in rows] == [fresh_key["candidate_hash"]]
    assert summary["mapped_server_calibrated_keys"] == 1
    assert summary["counts"]["rejected_server_calibrated_key"] == 1
    assert summary["calibrated_key_mappings"][0]["predicted_target_t"] == 10
    assert summary["calibrated_key_mappings"][0]["verified_target_t"] == 12


def test_selector_replaces_committed_candidate_with_uncommitted_same_key(tmp_path):
    committed = _candidate(1, 5, 10, 0, 2, 50)
    alternate = _candidate(1, 5, 10, 4, 3, 100)
    dry_run = _candidate(2, 7, 11, 0, 4, 60)
    source = tmp_path / "candidates.jsonl"
    _write_jsonl(source, [committed, alternate, dry_run])

    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        committed_hash = ledger.upsert_candidate(committed)
        ledger.persist_batch(
            "real-batch",
            [committed_hash],
            payload_hash([committed["coefficients"]]),
            dry_run=False,
        )
        dry_hash = ledger.upsert_candidate(dry_run)
        ledger.persist_batch(
            "dry-batch",
            [dry_hash],
            payload_hash([dry_run["coefficients"]]),
            dry_run=True,
        )

    output = tmp_path / "pilot.jsonl"
    summary = select_character_calibration_pilot([source], output, db_path=db)
    hashes = {
        json.loads(line)["candidate_hash"]
        for line in output.read_text(encoding="utf-8").splitlines()
    }
    assert hashes == {alternate["candidate_hash"], dry_run["candidate_hash"]}
    assert summary["counts"]["rejected_committed_candidate"] == 1


def test_selector_requires_explicit_polynomial_validity_proofs(tmp_path):
    rows = [
        _candidate(1, 2, 10, 0, 2, 100, local_irreducible=False),
        _candidate(2, 3, 11, 0, 3, 100, submission_ready=False),
        _candidate(3, 5, 12, 0, 4, 100, exact_compatibility_proven=False),
        _candidate(4, 7, 13, 0, 5, 100, valid_probability=0.99),
        _candidate(5, 11, 14, 0, 6, 100, predicted_target_t=15),
    ]
    source = tmp_path / "candidates.jsonl"
    _write_jsonl(source, rows)
    db = tmp_path / "control.sqlite3"
    with Ledger(db):
        pass

    output = tmp_path / "pilot.jsonl"
    summary = select_character_calibration_pilot([source], output, db_path=db)
    assert output.read_text(encoding="utf-8") == ""
    assert summary["counts"]["rejected_not_locally_irreducible"] == 1
    assert summary["counts"]["rejected_not_submission_ready"] == 1
    assert summary["counts"]["rejected_not_exactly_classified"] == 1
    assert summary["counts"]["rejected_validity_not_accepted"] == 1
    assert summary["counts"]["rejected_predicted_target_mismatch"] == 1


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"candidate_hash": "0" * 64}, "hash mismatch"),
        ({"local_root_count": 2}, "root mismatch"),
        ({"field_disc_abs": None, "estimated_nfdisc_abs": None}, "no accepted field discriminant"),
        ({"field_disc_abs": 100, "estimated_nfdisc_abs": 200}, "conflicting field discriminants"),
    ],
)
def test_selector_fails_closed_on_integrity_errors(tmp_path, updates, message):
    row = _candidate(1, 5, 10, 0, 2, 100, **updates)
    source = tmp_path / "candidates.jsonl"
    _write_jsonl(source, [row])
    db = tmp_path / "control.sqlite3"
    with Ledger(db):
        pass
    with pytest.raises(ValueError, match=message):
        select_character_calibration_pilot(
            [source],
            tmp_path / "pilot.jsonl",
            db_path=db,
        )


def test_selector_fails_on_server_root_mismatch_for_calibration(tmp_path):
    calibrated = _candidate(1, 5, 10, 0, 2, 100)
    source = tmp_path / "candidates.jsonl"
    _write_jsonl(source, [calibrated])
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        key = ledger.upsert_candidate(calibrated)
        ledger.record_verification(
            key,
            "bad-calibration",
            {"status": "accepted", "t": 10, "r": 2},
        )
    with pytest.raises(ValueError, match="root-count mismatch"):
        select_character_calibration_pilot(
            [source],
            tmp_path / "pilot.jsonl",
            db_path=db,
        )
