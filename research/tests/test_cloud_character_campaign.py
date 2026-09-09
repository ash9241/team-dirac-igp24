import hashlib
import json
from pathlib import Path

import pytest

import cloud.build_batch_job as batch
from cloud.calibrate_character_discoveries import calibrate_discoveries
from cloud.plan_character_campaign import plan_campaign
from cloud.plan_character_certification import plan_certification
from cloud.plan_priority_campaign import plan_priority_campaign
from cloud.plan_repair_campaign import plan_repair
from routeA.ledger import Ledger, candidate_hash


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_character_campaign_plan_is_base_and_lane_sharded(tmp_path):
    catalog = tmp_path / "routeA" / "data" / "catalog.jsonl"
    mapping = tmp_path / "routeA" / "data" / "map.jsonl"
    _write_jsonl(catalog, [
        {"base_t": 1, "label": "12.1.1", "coefficients": [0] * 12 + [1], "disc_abs": 6},
        {"base_t": 2, "label": "12.2.1", "coefficients": [0] * 12 + [1], "disc_abs": 10},
    ])
    _write_jsonl(mapping, [
        {"base_t": 1, "target_t": 10, "includes_permutation_sign": True, "includes_trivial_character": False},
        {"base_t": 1, "target_t": 11, "includes_permutation_sign": False, "includes_trivial_character": True},
        {"base_t": 1, "target_t": 12, "includes_permutation_sign": False, "includes_trivial_character": False},
        {"base_t": 2, "target_t": 20, "includes_permutation_sign": True, "includes_trivial_character": False},
        {"base_t": 2, "target_t": 21, "includes_permutation_sign": False, "includes_trivial_character": True},
    ])
    output = tmp_path / "cloud" / "campaigns" / "pilot"
    summary = plan_campaign(
        campaign_id="pilot",
        output_dir=output,
        catalogs=[catalog],
        map_path=mapping,
        db_path=None,
        lanes=["linear", "norm"],
        bases=[1],
        norm_scale_shards=2,
        norm_scales=[67, 71, 73],
        norm_solutions_per_scale=2,
        norm_options_per_squareclass=6,
        project=tmp_path,
    )
    assert summary["task_count"] == 3
    assert summary["base_count"] == 1
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert [task["lane"] for task in matrix["tasks"]].count("linear") == 1
    assert [task["lane"] for task in matrix["tasks"]].count("norm") == 2
    assert {task["base_t"] for task in matrix["tasks"]} == {1}
    assert matrix["norm_scales"] == [67, 71, 73]
    assert matrix["norm_solutions_per_scale"] == 2
    assert len({task["job_id"] for task in matrix["tasks"]}) == 3
    for task in matrix["tasks"]:
        manifest = json.loads((tmp_path / task["manifest"]).read_text(encoding="utf-8"))
        assert "--base" in manifest["args"]
        assert manifest["args"][manifest["args"].index("--base") + 1] == "1"
        if task["lane"] == "norm":
            assert manifest["args"][manifest["args"].index("--solutions-per-scale") + 1] == "2"
            assert manifest["args"][manifest["args"].index("--options-per-squareclass") + 1] == "6"
        assert all(len(row["sha256"]) == 64 for row in manifest["inputs"])
        assert "IGP24_API_KEY" not in json.dumps(manifest)


def test_character_campaign_can_shard_large_bases_by_catalog_rows(tmp_path):
    catalog = tmp_path / "routeA" / "data" / "catalog.jsonl"
    mapping = tmp_path / "routeA" / "data" / "map.jsonl"
    _write_jsonl(catalog, [
        {
            "base_t": 1,
            "label": f"12.1.{index}",
            "coefficients": [index] + [0] * 11 + [1],
            "disc_abs": 30,
        }
        for index in range(3)
    ])
    _write_jsonl(mapping, [
        {
            "base_t": 1,
            "target_t": 10,
            "includes_permutation_sign": False,
            "includes_trivial_character": False,
        }
    ])
    output = tmp_path / "cloud" / "campaigns" / "field-shards"
    summary = plan_campaign(
        campaign_id="field-shards",
        output_dir=output,
        catalogs=[catalog],
        map_path=mapping,
        db_path=None,
        lanes=["norm"],
        bases=[1],
        catalog_rows_per_task=1,
        norm_scale_shards=2,
        norm_scales=[67, 71],
        norm_equation_timeout=9,
        norm_initialization_timeout=90,
        project=tmp_path,
    )
    assert summary["task_count"] == 6
    assert summary["catalog_count"] == 1
    assert summary["catalog_shard_count"] == 3
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert matrix["catalog_rows_per_task"] == 1
    shard_paths = {task["catalog"] for task in matrix["tasks"]}
    assert len(shard_paths) == 3
    for task in matrix["tasks"]:
        shard_rows = (tmp_path / task["catalog"]).read_text().splitlines()
        assert len(shard_rows) == 1
        manifest = json.loads((tmp_path / task["manifest"]).read_text())
        assert manifest["args"][manifest["args"].index("--catalog") + 1] == task["catalog"]
        assert manifest["args"][manifest["args"].index("--equation-timeout") + 1] == "9"
        assert manifest["args"][manifest["args"].index("--initialization-timeout") + 1] == "90"


def test_batch_job_packs_tasks_onto_spot_vms(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "PROJECT", tmp_path)
    matrix_path = tmp_path / "cloud" / "campaigns" / "pilot" / "matrix.json"
    matrix_path.parent.mkdir(parents=True)
    matrix_path.write_text(json.dumps({
        "campaign_id": "pilot",
        "task_count": 17,
        "tasks": [{} for _ in range(17)],
    }), encoding="utf-8")
    output = tmp_path / "batch.json"
    summary = batch.build_batch_job(
        matrix_path,
        output,
        bucket="igp24-results",
        package_object="campaigns/pilot/input.tar.gz",
        results_prefix="campaigns/pilot/results",
        parallelism=16,
        tasks_per_node=8,
    )
    assert summary["maximum_vms"] == 2
    config = json.loads(output.read_text(encoding="utf-8"))
    group = config["taskGroups"][0]
    assert group["taskCount"] == 17
    assert group["parallelism"] == 16
    assert group["taskCountPerNode"] == 8
    policy = config["allocationPolicy"]["instances"][0]["policy"]
    assert policy["provisioningModel"] == "SPOT"
    script = group["taskSpec"]["runnables"][0]["script"]["text"]
    assert script.startswith("set -eu\n")
    assert "pipefail" not in script
    assert "BATCH_TASK_INDEX" in script
    assert "unset IGP24_API_KEY IGP24_API_KEY_FILE" in script
    assert "bash -eu -c" in script
    assert "Acquire::Retries=5" in script
    assert "DPkg::Lock::Timeout=600" in script
    assert '/usr/bin/python3 -c "import sympy"' in script


def test_batch_job_can_use_pari_only_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "PROJECT", tmp_path)
    matrix_path = tmp_path / "cloud" / "campaigns" / "pair" / "matrix.json"
    matrix_path.parent.mkdir(parents=True)
    matrix_path.write_text(json.dumps({
        "campaign_id": "pair",
        "task_count": 1,
        "tasks": [{}],
    }), encoding="utf-8")
    output = tmp_path / "batch.json"
    summary = batch.build_batch_job(
        matrix_path,
        output,
        bucket="igp24-results",
        package_object="campaigns/pair/input.tar.gz",
        results_prefix="campaigns/pair/results",
        bootstrap_profile="pari",
    )
    script = json.loads(output.read_text())["taskGroups"][0]["taskSpec"]["runnables"][0]["script"]["text"]
    assert summary["bootstrap_profile"] == "pari"
    assert "igp24-bootstrap-pari-v1" in script
    assert "python3 pari-gp pari-galdata" in script
    assert "python3-sympy" not in script
    assert "DPkg::Lock::Timeout=600" in script
    assert "gap-transgrp" not in script


def test_batch_job_can_build_pinned_pari217(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "PROJECT", tmp_path)
    matrix_path = tmp_path / "cloud" / "campaigns" / "pair" / "matrix.json"
    matrix_path.parent.mkdir(parents=True)
    matrix_path.write_text(json.dumps({
        "campaign_id": "pair",
        "task_count": 1,
        "tasks": [{}],
    }), encoding="utf-8")
    output = tmp_path / "batch.json"
    summary = batch.build_batch_job(
        matrix_path,
        output,
        bucket="igp24-results",
        package_object="campaigns/pair/input.tar.gz",
        results_prefix="campaigns/pair/results",
        bootstrap_profile="pari217",
    )
    script = json.loads(output.read_text())["taskGroups"][0]["taskSpec"]["runnables"][0]["script"]["text"]
    assert summary["bootstrap_profile"] == "pari217"
    assert "igp24-bootstrap-pari217-v1" in script
    assert "pari-2.17.2.tar.gz" in script
    assert "7d30578f5cf97b137a281f4548d131aafc0cde86bcfd10cc1e1bd72a81e65061" in script
    assert "ln -sf /opt/pari217/bin/gp /usr/bin/gp" in script


def test_repair_campaign_reindexes_only_requested_tasks(tmp_path):
    manifests = tmp_path / "cloud" / "campaigns" / "source" / "manifests"
    manifests.mkdir(parents=True)
    tasks = []
    for index in range(4):
        manifest = manifests / f"task-{index}.json"
        manifest.write_text(json.dumps({"job_id": f"job-{index}"}), encoding="utf-8")
        tasks.append({
            "index": index,
            "job_id": f"job-{index}",
            "manifest": str(manifest.relative_to(tmp_path)),
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "lane": "norm",
        })
    source = tmp_path / "cloud" / "campaigns" / "source" / "matrix.json"
    source.write_text(json.dumps({
        "campaign_id": "source",
        "task_count": len(tasks),
        "tasks": tasks,
        "inputs": [],
    }), encoding="utf-8")
    output = tmp_path / "cloud" / "campaigns" / "repair"
    summary = plan_repair(
        source,
        [3, 1, 3],
        campaign_id="repair",
        output_dir=output,
        project=tmp_path,
    )
    assert summary["repair_indices"] == [1, 3]
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert [task["index"] for task in matrix["tasks"]] == [0, 1]
    assert [task["source_index"] for task in matrix["tasks"]] == [1, 3]
    assert [task["job_id"] for task in matrix["tasks"]] == ["job-1", "job-3"]


def test_priority_campaign_interleaves_target_breadth_before_alternatives(tmp_path):
    manifests = tmp_path / "cloud" / "manifests"
    manifests.mkdir(parents=True)
    tasks = {}
    for job_id, target_t, opportunity in (
        ("target-10-a", 10, 2.0),
        ("target-10-b", 10, 2.0),
        ("target-20-a", 20, 5.0),
        ("target-30-a", 30, 3.0),
    ):
        manifest = manifests / f"{job_id}.json"
        manifest.write_text(json.dumps({"job_id": job_id}), encoding="utf-8")
        tasks[job_id] = {
            "index": 99,
            "job_id": job_id,
            "target_t": target_t,
            "live_opportunity": opportunity,
            "source_candidate_hash": job_id,
            "manifest": str(manifest.relative_to(tmp_path)),
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        }
    source_a = tmp_path / "cloud" / "a.json"
    source_b = tmp_path / "cloud" / "b.json"
    source_a.write_text(json.dumps({
        "campaign_id": "a",
        "tasks": [tasks["target-10-a"], tasks["target-10-b"], tasks["target-20-a"]],
        "inputs": [],
    }), encoding="utf-8")
    source_b.write_text(json.dumps({
        "campaign_id": "b",
        "tasks": [tasks["target-20-a"], tasks["target-30-a"]],
        "inputs": [],
    }), encoding="utf-8")

    summary = plan_priority_campaign(
        [source_a, source_b],
        campaign_id="priority",
        output_dir=tmp_path / "cloud" / "priority",
        project=tmp_path,
    )

    matrix = json.loads(
        (tmp_path / "cloud" / "priority" / "matrix.json").read_text(encoding="utf-8")
    )
    assert [task["job_id"] for task in matrix["tasks"]] == [
        "target-20-a",
        "target-30-a",
        "target-10-a",
        "target-10-b",
    ]
    assert [task["index"] for task in matrix["tasks"]] == [0, 1, 2, 3]
    assert [task["priority_alternative_index"] for task in matrix["tasks"]] == [0, 0, 0, 1]
    assert summary["duplicate_source_tasks"] == 1
    assert summary["first_400_targets"] == 3
    assert summary["target_ceiling"] == 10.0


def test_pilot_certification_chooses_highest_value_signature(tmp_path):
    discoveries = tmp_path / "cloud" / "campaigns" / "found.jsonl"
    _write_jsonl(discoveries, [{
        "base_t": 1,
        "starting_target_t": 12,
        "label": "12.1.1",
        "norm_squareclass": 5,
        "base_coefficients": [0] * 12 + [1],
        "seed_coefficients": [1, 1],
    }])
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        ledger.record_target_snapshot([
            {
                "t": 12,
                "r": root,
                "team_count": 0 if root == 8 else 2,
                "baseline": False,
            }
            for root in range(0, 25, 4)
        ], snapshot_id="snapshot", captured_at="2026-07-15T00:00:00+00:00")
    output = tmp_path / "cloud" / "campaigns" / "certify"
    summary = plan_certification(
        discoveries,
        campaign_id="certify",
        output_dir=output,
        db_path=db,
        pilot_only=True,
        project=tmp_path,
    )
    assert summary["task_count"] == 1
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / matrix["tasks"][0]["manifest"]).read_text(encoding="utf-8"))
    assert "--pilot-only" in manifest["args"]
    source = tmp_path / manifest["inputs"][0]["path"]
    row = json.loads(source.read_text(encoding="utf-8"))
    assert row["pilot_target_r"] == 8


def test_pilot_certification_can_shard_by_character_key_with_alternatives(tmp_path):
    discoveries = tmp_path / "cloud" / "campaigns" / "found.jsonl"
    _write_jsonl(discoveries, [
        {
            "base_t": base_t,
            "starting_target_t": 12,
            "label": f"12.{base_t}.{seed}",
            "norm_squareclass": squareclass,
            "base_coefficients": [0] * 12 + [1],
            "seed_coefficients": [seed, 1],
        }
        for base_t, squareclass in ((1, 5), (2, 7))
        for seed in (1, 2, 3)
    ])
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        ledger.record_target_snapshot([
            {"t": 12, "r": root, "team_count": 0, "baseline": False}
            for root in range(0, 25, 4)
        ], snapshot_id="snapshot", captured_at="2026-07-15T00:00:00+00:00")
    output = tmp_path / "cloud" / "campaigns" / "certify-by-key"
    excluded = tmp_path / "cloud" / "campaigns" / "prior" / "sources"
    _write_jsonl(excluded / "attempted.jsonl", [{
        "base_t": 1,
        "starting_target_t": 12,
        "label": "12.1.1",
        "norm_squareclass": 5,
        "base_coefficients": [0] * 12 + [1],
        "seed_coefficients": [1, 1],
    }])
    summary = plan_certification(
        discoveries,
        campaign_id="certify-by-key",
        output_dir=output,
        db_path=db,
        pilot_only=True,
        pilot_by_key=True,
        alternatives_per_target=2,
        exclude_discoveries_paths=[excluded],
        project=tmp_path,
    )
    assert summary["task_count"] == 2
    assert summary["source_targets"] == 1
    assert summary["source_groups"] == 2
    assert summary["input_discoveries"] == 6
    assert summary["source_discoveries"] == 5
    assert summary["excluded_discoveries"] == 1
    assert summary["source_character_keys"] == 2
    assert summary["pilot_by_key"] is True
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert len({task["job_id"] for task in matrix["tasks"]}) == 2
    assert {
        (task["base_t"], task["norm_squareclass"])
        for task in matrix["tasks"]
    } == {(1, 5), (2, 7)}
    assigned_roots = set()
    for task in matrix["tasks"]:
        assert task["source_alternatives"] == 2
        manifest = json.loads(
            (tmp_path / task["manifest"]).read_text(encoding="utf-8")
        )
        rows = [
            json.loads(line)
            for line in (tmp_path / manifest["inputs"][0]["path"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(rows) == 2
        assert {
            (row["base_t"], row["norm_squareclass"])
            for row in rows
        } == {(task["base_t"], task["norm_squareclass"])}
        if task["base_t"] == 1:
            assert {row["seed_coefficients"][0] for row in rows} == {2, 3}
        assert len({row["pilot_target_r"] for row in rows}) == 1
        assigned_roots.add(rows[0]["pilot_target_r"])
    assert assigned_roots == {8, 12}


def test_certification_prefers_lower_discriminant_field(tmp_path):
    discoveries = tmp_path / "cloud" / "campaigns" / "found.jsonl"
    _write_jsonl(discoveries, [
        {
            "base_t": 1,
            "starting_target_t": 12,
            "label": label,
            "disc_abs": disc,
            "norm_squareclass": 1,
            "base_coefficients": [constant] + [0] * 11 + [1],
            "seed_coefficients": [1],
        }
        for label, disc, constant in (("large", 10**20, 1), ("small", 10**10, 2))
    ])
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        ledger.record_target_snapshot([
            {"t": 12, "r": root, "team_count": 0, "baseline": False}
            for root in range(0, 25, 4)
        ], snapshot_id="snapshot", captured_at="2026-07-15T00:00:00+00:00")
    output = tmp_path / "cloud" / "campaigns" / "certify-low-disc"
    plan_certification(
        discoveries,
        campaign_id="certify-low-disc",
        output_dir=output,
        db_path=db,
        pilot_only=True,
        project=tmp_path,
    )
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (tmp_path / matrix["tasks"][0]["manifest"]).read_text(encoding="utf-8")
    )
    source = tmp_path / manifest["inputs"][0]["path"]
    assert json.loads(source.read_text(encoding="utf-8"))["label"] == "small"


def test_server_calibration_relabels_and_filters_discoveries(tmp_path):
    discoveries = tmp_path / "discoveries.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    additional_candidates = tmp_path / "additional-candidates.jsonl"
    output = tmp_path / "calibrated.jsonl"
    unmapped = tmp_path / "unmapped.jsonl"
    db = tmp_path / "control.sqlite3"
    coefficients = [
        ",".join([str(constant)] + ["0"] * 23 + ["1"])
        for constant in (0, 1, 2)
    ]
    hashes = [candidate_hash(line) for line in coefficients]
    _write_jsonl(discoveries, [
        {
            "base_t": 1,
            "norm_squareclass": 5,
            "starting_target_t": 10,
            "pilot_terminal_t": 10,
            "label": "exact",
            "shift": 0,
        },
        {
            "base_t": 2,
            "norm_squareclass": 7,
            "starting_target_t": 21,
            "pilot_terminal_t": 21,
            "label": "relabel",
            "shift": 1,
        },
        {
            "base_t": 3,
            "norm_squareclass": 11,
            "starting_target_t": 30,
            "label": "unverified",
            "shift": 2,
        },
    ])
    _write_jsonl(candidates, [
        {
            "candidate_hash": hashes[0],
            "coefficients": coefficients[0],
            "parameters": {"base_t": 1, "norm_squareclass": 5},
            "target_t": 10,
            "target_r": 4,
        },
        {
            "candidate_hash": hashes[2],
            "coefficients": coefficients[2],
            "parameters": {"base_t": 3, "norm_squareclass": 11},
            "target_t": 30,
            "target_r": 12,
        },
    ])
    _write_jsonl(additional_candidates, [{
        "candidate_hash": hashes[1],
        "coefficients": coefficients[1],
        "parameters": {"base_t": 2, "norm_squareclass": 7},
        "target_t": 21,
        "target_r": 8,
    }])
    with Ledger(db) as ledger:
        for line in coefficients:
            ledger.upsert_candidate({"coefficients": line})
        ledger.record_verification(
            hashes[0], "sub-exact", {"status": "accepted", "t": 10, "r": 4}
        )
        ledger.record_verification(
            hashes[1], "sub-relabel", {"status": "accepted", "t": 22, "r": 8}
        )

    summary = calibrate_discoveries(
        discoveries,
        candidates,
        output,
        db_path=db,
        unmapped_output_path=unmapped,
        additional_candidates_paths=[additional_candidates],
    )
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary["calibration_keys"] == 2
    assert summary["exact_calibration_keys"] == 1
    assert summary["relabeled_calibration_keys"] == 1
    assert summary["unverified_candidates"] == 1
    assert summary["output_discoveries"] == 2
    assert summary["filtered_unmapped_discoveries"] == 1
    assert [(row["predicted_target_t"], row["starting_target_t"]) for row in rows] == [
        (10, 10),
        (21, 22),
    ]
    assert rows[1]["pilot_terminal_t"] == 22
    assert rows[1]["server_calibration"]["submission_id"] == "sub-relabel"
    unmapped_rows = [json.loads(line) for line in unmapped.read_text().splitlines()]
    assert [row["label"] for row in unmapped_rows] == ["unverified"]
    assert summary["unmapped_output"] == str(unmapped)
    assert summary["candidate_sources"] == [
        str(candidates),
        str(additional_candidates),
    ]


def test_server_calibration_rejects_root_mismatch(tmp_path):
    discoveries = tmp_path / "discoveries.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    output = tmp_path / "calibrated.jsonl"
    db = tmp_path / "control.sqlite3"
    line = ",".join(["0"] * 24 + ["1"])
    key = candidate_hash(line)
    _write_jsonl(discoveries, [{
        "base_t": 1,
        "norm_squareclass": 5,
        "starting_target_t": 10,
    }])
    _write_jsonl(candidates, [{
        "candidate_hash": key,
        "coefficients": line,
        "parameters": {"base_t": 1, "norm_squareclass": 5},
        "target_t": 10,
        "target_r": 4,
    }])
    with Ledger(db) as ledger:
        ledger.upsert_candidate({"coefficients": line})
        ledger.record_verification(
            key, "sub-root-mismatch", {"status": "accepted", "t": 10, "r": 8}
        )
    with pytest.raises(ValueError, match="root-count mismatch"):
        calibrate_discoveries(discoveries, candidates, output, db_path=db)


def test_server_calibration_keeps_distinct_targets_for_same_norm_key(tmp_path):
    discoveries = tmp_path / "discoveries.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    output = tmp_path / "calibrated.jsonl"
    db = tmp_path / "control.sqlite3"
    lines = [
        ",".join([str(constant)] + ["0"] * 23 + ["1"])
        for constant in (7, 8)
    ]
    hashes = [candidate_hash(line) for line in lines]
    _write_jsonl(discoveries, [
        {"base_t": 1, "norm_squareclass": 5, "starting_target_t": target}
        for target in (10, 11)
    ])
    _write_jsonl(candidates, [
        {
            "candidate_hash": key,
            "coefficients": line,
            "parameters": {"base_t": 1, "norm_squareclass": 5},
            "target_t": target,
            "target_r": 4,
        }
        for key, line, target in zip(hashes, lines, (10, 11))
    ])
    with Ledger(db) as ledger:
        for key, line, target in zip(hashes, lines, (10, 11)):
            ledger.upsert_candidate({"coefficients": line})
            ledger.record_verification(
                key,
                f"sub-{target}",
                {"status": "accepted", "t": target, "r": 4},
            )
    summary = calibrate_discoveries(
        discoveries,
        candidates,
        output,
        db_path=db,
    )
    assert summary["calibration_keys"] == 2
    assert summary["output_discoveries"] == 2
    assert {
        row["starting_target_t"]
        for row in map(json.loads, output.read_text().splitlines())
    } == {10, 11}


def test_server_calibration_allows_multiple_roots_for_same_character_key(tmp_path):
    discoveries = tmp_path / "discoveries.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    output = tmp_path / "calibrated.jsonl"
    db = tmp_path / "control.sqlite3"
    lines = [
        ",".join([str(constant)] + ["0"] * 23 + ["1"])
        for constant in (9, 10)
    ]
    hashes = [candidate_hash(line) for line in lines]
    _write_jsonl(discoveries, [{
        "base_t": 1,
        "norm_squareclass": 5,
        "starting_target_t": 10,
    }])
    _write_jsonl(candidates, [
        {
            "candidate_hash": key,
            "coefficients": line,
            "parameters": {"base_t": 1, "norm_squareclass": 5},
            "target_t": 10,
            "target_r": root,
        }
        for key, line, root in zip(hashes, lines, (4, 8))
    ])
    with Ledger(db) as ledger:
        for key, line, root in zip(hashes, lines, (4, 8)):
            ledger.upsert_candidate({"coefficients": line})
            ledger.record_verification(
                key,
                f"sub-{root}",
                {"status": "accepted", "t": 10, "r": root},
            )
    summary = calibrate_discoveries(
        discoveries,
        candidates,
        output,
        db_path=db,
    )
    assert summary["calibration_keys"] == 1
    assert summary["mappings"][0]["verified_target_rs"] == [4, 8]
