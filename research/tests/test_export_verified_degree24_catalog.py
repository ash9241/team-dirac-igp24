import json

from routeA.export_verified_degree24_catalog import export_verified_catalog
from routeA.ledger import Ledger, candidate_hash


def test_verified_catalog_keeps_lowest_discriminant_per_group(tmp_path) -> None:
    db = tmp_path / "control.sqlite3"
    lines = [
        ",".join([str(constant)] + ["0"] * 23 + ["1"])
        for constant in (1, 2, 3)
    ]
    with Ledger(db) as ledger:
        for index, (line, disc, target) in enumerate(
            zip(lines, (1000, 100, 50), (10, 10, 11))
        ):
            key = ledger.upsert_candidate({"coefficients": line})
            ledger.record_verification(key, f"sub-{index}", {
                "status": "accepted",
                "t": target,
                "r": 4 * index,
                "fieldDiscAbs": str(disc),
            })
    output = tmp_path / "verified.jsonl"
    summary = export_verified_catalog(output, db_path=db)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary["source_groups"] == 2
    assert len(rows) == 2
    assert next(row for row in rows if row["source_t"] == 10)["disc_abs"] == 100
    assert {row["candidate_hash"] for row in rows} == {
        candidate_hash(lines[1]),
        candidate_hash(lines[2]),
    }


def test_verified_catalog_can_prefer_distinct_root_signatures(tmp_path) -> None:
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        for index, (root, disc) in enumerate(((0, 10), (0, 20), (4, 100))):
            line = ",".join(
                [str(index + 1)] + ["0"] * 23 + ["1"]
            )
            key = ledger.upsert_candidate({"coefficients": line})
            ledger.record_verification(key, f"sub-{index}", {
                "status": "accepted",
                "t": 8,
                "r": root,
                "fieldDiscAbs": str(disc),
            })
    output = tmp_path / "verified.jsonl"
    export_verified_catalog(
        output,
        db_path=db,
        fields_per_group=2,
        prefer_distinct_roots=True,
    )
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert {(row["source_r"], row["disc_abs"]) for row in rows} == {
        (0, 10),
        (4, 100),
    }
