import json

import pytest

from cloud.export_server_relabelled_seed_families import (
    export_server_relabelled_seed_families,
)
from routeA.ledger import Ledger, candidate_hash


def _row(target_t=10, target_r=4, constant=2, **updates):
    coefficients = [constant] + [0] * 23 + [1]
    line = ",".join(str(value) for value in coefficients)
    row = {
        "base_coefficients": [1] + [0] * 11 + [1],
        "candidate_hash": candidate_hash(line),
        "coefficients": line,
        "exact_compatibility_proven": True,
        "field_disc_abs": 100,
        "h_coefficients": ["1"] + ["0"] * 11,
        "local_irreducible": True,
        "local_root_count": target_r,
        "norm_squareclass": 5,
        "parameters": {"base_t": 3},
        "submission_ready": True,
        "target_r": target_r,
        "target_t": target_t,
    }
    row.update(updates)
    return row


def _write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_exporter_keeps_only_accepted_root_matching_relabels(tmp_path):
    relabel = _row()
    exact = _row(target_t=20, constant=3)
    source = tmp_path / "candidates.jsonl"
    _write(source, [relabel, exact, relabel])
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        for row, verified_t in ((relabel, 12), (exact, 20)):
            key = ledger.upsert_candidate(row)
            ledger.record_verification(
                key,
                "sub",
                {"status": "accepted", "t": verified_t, "r": row["target_r"]},
            )

    output = tmp_path / "discoveries.jsonl"
    summary = export_server_relabelled_seed_families([source], output, db_path=db)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["starting_target_t"] == 12
    assert rows[0]["server_calibration"]["predicted_target_t"] == 10
    assert rows[0]["server_calibration"]["verified_target_r"] == 4
    assert rows[0]["server_calibrated"] is True
    assert summary["counts"]["exported_seed_families"] == 1
    assert summary["counts"]["duplicate_candidate_rows"] == 1
    assert summary["counts"]["accepted_not_relabelled"] == 1


def test_exporter_fails_closed_on_server_root_mismatch(tmp_path):
    row = _row()
    source = tmp_path / "candidates.jsonl"
    _write(source, [row])
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        key = ledger.upsert_candidate(row)
        ledger.record_verification(
            key,
            "sub",
            {"status": "accepted", "t": 12, "r": 8},
        )
    with pytest.raises(ValueError, match="root-count mismatch"):
        export_server_relabelled_seed_families(
            [source], tmp_path / "discoveries.jsonl", db_path=db
        )


def test_exporter_skips_accepted_non_character_construction(tmp_path):
    row = _row()
    row.pop("h_coefficients")
    source = tmp_path / "candidates.jsonl"
    _write(source, [row])
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        key = ledger.upsert_candidate(row)
        ledger.record_verification(
            key,
            "sub",
            {"status": "accepted", "t": 12, "r": 4},
        )
    output = tmp_path / "discoveries.jsonl"
    summary = export_server_relabelled_seed_families([source], output, db_path=db)
    assert output.read_text() == ""
    assert summary["counts"]["accepted_non_seed_rows"] == 1


def test_exporter_fails_closed_on_hash_mismatch(tmp_path):
    row = _row(candidate_hash="0" * 64)
    source = tmp_path / "candidates.jsonl"
    _write(source, [row])
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        # Install an accepted row under the supplied hash so validation reaches
        # the independent canonical hash check.
        ledger.connection.execute(
            """INSERT INTO candidate(candidate_hash, coefficients, generated_at)
               VALUES (?, ?, ?)""",
            (row["candidate_hash"], row["coefficients"], "now"),
        )
        ledger.connection.commit()
        ledger.record_verification(
            row["candidate_hash"],
            "sub",
            {"status": "accepted", "t": 12, "r": 4},
        )
    with pytest.raises(ValueError, match="hash mismatch"):
        export_server_relabelled_seed_families(
            [source], tmp_path / "discoveries.jsonl", db_path=db
        )
