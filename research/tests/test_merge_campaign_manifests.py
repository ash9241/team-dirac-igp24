from __future__ import annotations

import json

from routeA.merge_campaign_manifests import merge_rows


def test_merge_rows_deduplicates_polynomials_but_keeps_alternates(tmp_path) -> None:
    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"
    first = {
        "candidate_hash": "a",
        "coefficients": "1,0,1",
        "target_t": 10,
        "target_r": 0,
        "field_disc_abs": 100,
        "submission_ready": True,
        "exact_compatibility_proven": True,
        "modular_witnesses": [],
    }
    richer = dict(first, modular_witnesses=[{"prime": 2}])
    alternate = dict(
        first,
        candidate_hash="b",
        coefficients="2,0,1",
        field_disc_abs=80,
    )
    left.write_text(json.dumps(first) + "\n", encoding="utf-8")
    right.write_text(
        json.dumps(richer) + "\n" + json.dumps(alternate) + "\n",
        encoding="utf-8",
    )

    rows = merge_rows([left, right])
    assert [row["candidate_hash"] for row in rows] == ["b", "a"]
    assert rows[1]["modular_witnesses"] == [{"prime": 2}]
