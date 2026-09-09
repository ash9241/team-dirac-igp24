import json

from routeA.ledger import candidate_hash
from routeA.select_subfield_exploration import select_subfield_exploration


def _candidate(base_t, target_t, constant):
    coefficients = ",".join(map(str, [constant] + [0] * 23 + [1]))
    return {
        "base_t": base_t,
        "candidate_hash": candidate_hash(coefficients),
        "coefficients": coefficients,
        "field_disc_abs": 100,
        "local_irreducible": True,
        "local_root_count": 0,
        "norm_squareclass": 1,
        "parameters": {"subfield_degree": 6, "subfield_index": 1},
        "target_r": 0,
        "target_t": target_t,
    }


def test_selector_deduplicates_polynomial_with_conflicting_predictions(tmp_path):
    low_opportunity = _candidate(1, 101, 2)
    high_opportunity = dict(low_opportunity, base_t=2, target_t=202)
    source = tmp_path / "candidates.jsonl"
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in [low_opportunity, high_opportunity]),
        encoding="utf-8",
    )
    atlas = tmp_path / "atlas.jsonl"
    atlas.write_text(
        "".join(
            json.dumps(
                {
                    "status": "architecture_gap",
                    "score_ceiling": ceiling,
                    "block_quotients": [
                        {"block_size": 2, "quotient_degree": 12, "quotient_t": base_t}
                    ],
                }
            )
            + "\n"
            for base_t, ceiling in [(1, 1.0), (2, 9.0)]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "selected.jsonl"

    summary = select_subfield_exploration(
        [source],
        output,
        atlas_path=atlas,
        db_path=tmp_path / "ledger.sqlite3",
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["base_t"] == 2
    assert rows[0]["target_t"] == 202
    assert summary["duplicate_candidate_rows"] == 1
    assert summary["unique_candidates"] == 1
