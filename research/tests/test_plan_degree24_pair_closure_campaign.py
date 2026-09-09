import json
from pathlib import Path

import pytest

from cloud.plan_degree24_pair_closure_campaign import (
    normalize_closure_sources,
    plan_pair_closure_campaign,
)
from routeA.ledger import Ledger, candidate_hash


def _candidate(t: int, r: int, constant: int, disc: int, **updates):
    coefficients = [constant] + [0] * 23 + [1]
    line = ",".join(str(value) for value in coefficients)
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
        "recipe_family": "test-exact-family",
        "recipe_lineage": f"test:{constant}",
    }
    row.update(updates)
    return row


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_closure_normalizer_is_exact_and_infers_processed_parents():
    parent = _candidate(2, 0, 2, 101)
    child = _candidate(
        3,
        2,
        3,
        103,
        recipe_family="degree24_pair_sum_resolvent",
        recipe_lineage=f"pair24:{parent['candidate_hash']}",
        parameters={"source_candidate_hash": parent["candidate_hash"]},
    )
    not_exact = _candidate(4, 0, 4, 107, exact_compatibility_proven=False)
    not_ready = _candidate(5, 0, 5, 109, submission_ready=False)
    reducible = _candidate(6, 0, 6, 113, local_irreducible=False)

    sources, counts, processed = normalize_closure_sources(
        [parent, child, not_exact, not_ready, reducible]
    )

    assert [row["candidate_hash"] for row in sources] == [child["candidate_hash"]]
    assert sources[0]["source_t"] == 3
    assert sources[0]["source_r"] == 2
    assert sources[0]["coefficients"] == [3] + [0] * 23 + [1]
    assert sources[0]["disc_abs"] == 103
    assert parent["candidate_hash"] in processed
    assert counts["rejected_already_processed"] == 1
    assert counts["rejected_not_exact"] == 1
    assert counts["rejected_not_submission_ready"] == 1
    assert counts["rejected_not_locally_irreducible"] == 1


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"candidate_hash": "0" * 64}, "hash mismatch"),
        ({"local_root_count": 2}, "root mismatch"),
        ({"estimated_nfdisc_abs": None}, "no certified field discriminant"),
        (
            {"field_disc_abs": 101, "estimated_nfdisc_abs": 103},
            "conflicting field discriminants",
        ),
    ],
)
def test_closure_normalizer_fails_closed_on_integrity_errors(updates, message):
    row = _candidate(2, 0, 2, 103, **updates)
    with pytest.raises(ValueError, match=message):
        normalize_closure_sources([row])


def test_closure_planner_preserves_owned_and_zero_opportunity_seeds(tmp_path):
    candidates = tmp_path / "cloud" / "results" / "candidates.jsonl"
    orbit_map = tmp_path / "routeA" / "data" / "pair-map.jsonl"
    source_self = _candidate(2, 0, 2, 101)
    source_live = _candidate(3, 2, 3, 103)
    source_ambiguous = _candidate(4, 0, 4, 107)
    source_ordered = _candidate(5, 0, 5, 109)
    _write_jsonl(
        candidates,
        [source_self, source_live, source_ambiguous, source_ordered],
    )
    _write_jsonl(
        orbit_map,
        [
            {
                "source_t": 2,
                "orbit_targets": [2],
                "orbit_count": 1,
                "unambiguous_target_t": 2,
                "evidence": "gap-exact-pair-action",
            },
            {
                "source_t": 3,
                "orbit_targets": [10],
                "orbit_count": 1,
                "unambiguous_target_t": 10,
                "evidence": "gap-exact-pair-action",
            },
            {
                "source_t": 4,
                "orbit_targets": [11, 12],
                "orbit_count": 2,
                "unambiguous_target_t": None,
                "evidence": "gap-exact-pair-action",
            },
            {
                "source_t": 5,
                "orbit_targets": [5],
                "orbit_count": 1,
                "unambiguous_target_t": 5,
                "evidence": "gap-exact-ordered-pair-action",
            },
        ],
    )

    db = tmp_path / "routeA" / "data" / "control.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    with Ledger(db) as ledger:
        ledger.record_target_snapshot(
            [
                {"t": 2, "r": 0, "team_count": 1, "baseline": False},
                {"t": 10, "r": 0, "team_count": 0, "baseline": False},
            ],
            snapshot_id="snapshot",
            captured_at="2026-07-15T12:00:00+00:00",
        )
        key = ledger.upsert_candidate(source_self)
        ledger.record_verification(
            key,
            "owned-submission",
            {"status": "accepted", "t": 2, "r": 0},
        )

    output = tmp_path / "cloud" / "campaigns" / "closure"
    summary = plan_pair_closure_campaign(
        [candidates],
        orbit_map,
        campaign_id="closure",
        output_dir=output,
        db_path=db,
        project=tmp_path,
    )

    assert summary["task_count"] == 2
    assert summary["normalized_frontier_source_count"] == 4
    assert summary["missing_unambiguous_action_sources"] == 2
    assert summary["owned_source_seeds_preserved"] == 1
    assert summary["zero_immediate_opportunity_tasks_preserved"] == 1
    assert summary["self_action_tasks"] == 1
    assert summary["unique_target_live_opportunity"] == 1.0
    assert summary["action_counts"]["rejected_ambiguous_pair_action"] == 1
    assert summary["action_counts"]["rejected_non_unordered_pair_action"] == 1

    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert matrix["action_kind"] == "unordered-pair"
    assert matrix["include_self_maps"] is True
    assert matrix["preserve_owned_closure_seeds"] is True
    tasks = {task["source_t"]: task for task in matrix["tasks"]}
    assert tasks[2]["source_pair_owned"] is True
    self_manifest = json.loads(
        (tmp_path / tasks[2]["manifest"]).read_text(encoding="utf-8")
    )
    assert self_manifest["args"][-1] == "--allow-self-map"
    assert self_manifest["campaign"][
        "preserved_as_closure_seed_regardless_of_ownership"
    ] is True
    assert "IGP24_API_KEY" not in json.dumps(matrix)


def test_closure_pilot_limit_ranks_opportunity_but_preserves_frontier(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    orbit_map = tmp_path / "pair-map.jsonl"
    low = _candidate(2, 0, 2, 101)
    high = _candidate(3, 0, 3, 103)
    _write_jsonl(candidates, [low, high])
    _write_jsonl(
        orbit_map,
        [
            {
                "source_t": 2,
                "orbit_targets": [20],
                "orbit_count": 1,
                "unambiguous_target_t": 20,
                "evidence": "gap-exact-pair-action",
            },
            {
                "source_t": 3,
                "orbit_targets": [30],
                "orbit_count": 1,
                "unambiguous_target_t": 30,
                "evidence": "gap-exact-pair-action",
            },
        ],
    )
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        ledger.record_target_snapshot(
            [
                {"t": 20, "r": 0, "team_count": 2, "baseline": False},
                {"t": 30, "r": 0, "team_count": 0, "baseline": False},
            ],
            snapshot_id="snapshot",
            captured_at="2026-07-15T12:00:00+00:00",
        )

    output = tmp_path / "campaign"
    summary = plan_pair_closure_campaign(
        [candidates],
        orbit_map,
        campaign_id="pilot",
        output_dir=output,
        db_path=db,
        task_limit=1,
        project=tmp_path,
    )
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    normalized = [
        json.loads(line)
        for line in (output / "normalized_frontier_sources.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert matrix["tasks"][0]["source_t"] == 3
    assert summary["eligible_source_count_before_limit"] == 2
    assert summary["deferred_by_task_limit"] == 1
    assert len(normalized) == 2


def test_closure_planner_can_use_generic_pair_invariant(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    orbit_map = tmp_path / "pair-map.jsonl"
    source = _candidate(2, 0, 2, 101)
    _write_jsonl(candidates, [source])
    _write_jsonl(
        orbit_map,
        [
            {
                "source_t": 2,
                "orbit_targets": [3],
                "orbit_count": 1,
                "unambiguous_target_t": 3,
                "evidence": "gap-exact-pair-action",
            }
        ],
    )

    output = tmp_path / "campaign"
    plan_pair_closure_campaign(
        [candidates],
        orbit_map,
        campaign_id="generic-closure",
        output_dir=output,
        db_path=None,
        resolvent_kind="pair-sum-product",
        product_weight=7,
        project=tmp_path,
    )

    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (tmp_path / matrix["tasks"][0]["manifest"]).read_text(encoding="utf-8")
    )
    assert matrix["action_kind"] == "generic-unordered-pair"
    assert matrix["resolvent_kind"] == "pair-sum-product"
    assert matrix["product_weight"] == 7
    assert manifest["args"][-4:] == [
        "--resolvent-kind",
        "pair-sum-product",
        "--product-weight",
        "7",
    ]


def test_closure_planner_dedupes_completed_matrix_sources(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    orbit_map = tmp_path / "pair-map.jsonl"
    processed_matrix = tmp_path / "completed-matrix.json"
    first = _candidate(2, 0, 2, 101)
    second = _candidate(3, 0, 3, 103)
    _write_jsonl(candidates, [first, second])
    _write_jsonl(
        orbit_map,
        [
            {
                "source_t": source_t,
                "orbit_targets": [source_t],
                "orbit_count": 1,
                "unambiguous_target_t": source_t,
                "evidence": "gap-exact-pair-action",
            }
            for source_t in (2, 3)
        ],
    )
    processed_matrix.write_text(
        json.dumps({"tasks": [{"source_candidate_hash": first["candidate_hash"]}]}),
        encoding="utf-8",
    )

    output = tmp_path / "campaign"
    summary = plan_pair_closure_campaign(
        [candidates],
        orbit_map,
        campaign_id="dedupe",
        output_dir=output,
        db_path=None,
        processed_matrix_paths=[processed_matrix],
        project=tmp_path,
    )
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert [task["source_candidate_hash"] for task in matrix["tasks"]] == [
        second["candidate_hash"]
    ]
    assert summary["normalization_counts"]["rejected_already_processed"] == 1
