import json
from pathlib import Path

from cloud.plan_degree24_pair_resolvent_campaign import plan_pair_resolvent_campaign
from routeA.ledger import Ledger


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_pair_resolvent_plan_selects_low_disc_exact_source(tmp_path):
    catalog = tmp_path / "routeA" / "data" / "verified.jsonl"
    orbit_map = tmp_path / "routeA" / "data" / "pair-map.jsonl"
    _write_jsonl(catalog, [
        {
            "source_t": source_t,
            "source_r": 0,
            "coefficients": [source_t] + [0] * 23 + [1],
            "disc_abs": disc,
            "candidate_hash": f"hash-{source_t}",
        }
        for source_t, disc in ((1, 100), (2, 50), (3, 25), (4, 10))
    ])
    _write_jsonl(orbit_map, [
        {
            "source_t": 1,
            "orbit_targets": [10],
            "orbit_count": 1,
            "unambiguous_target_t": 10,
        },
        {
            "source_t": 2,
            "orbit_targets": [10, 10],
            "orbit_count": 2,
            "unambiguous_target_t": 10,
        },
        {
            "source_t": 3,
            "orbit_targets": [3],
            "orbit_count": 1,
            "unambiguous_target_t": 3,
        },
        {
            "source_t": 4,
            "orbit_targets": [11, 12],
            "orbit_count": 2,
            "unambiguous_target_t": None,
        },
    ])
    db = tmp_path / "control.sqlite3"
    with Ledger(db) as ledger:
        ledger.record_target_snapshot([
            {
                "t": 10,
                "r": root,
                "team_count": 0 if root == 0 else 1,
                "baseline": root == 4,
            }
            for root in range(0, 25, 2)
        ], snapshot_id="snapshot", captured_at="2026-07-15T00:00:00+00:00")

    output = tmp_path / "cloud" / "campaigns" / "pair-pilot"
    summary = plan_pair_resolvent_campaign(
        catalog,
        orbit_map,
        campaign_id="pair-pilot",
        output_dir=output,
        db_path=db,
        sources_per_target=1,
        project=tmp_path,
    )
    assert summary["task_count"] == 1
    assert summary["source_count"] == 1
    assert summary["target_count"] == 1
    assert summary["rejected_self_maps"] == 1
    assert summary["rejected_ambiguous_maps"] == 1
    # r=0 contributes 1, eleven non-baseline remaining roots contribute 1/2.
    assert summary["live_opportunity"] == 6.5

    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    assert matrix["tasks"][0]["source_t"] == 2
    assert matrix["tasks"][0]["target_t"] == 10
    manifest = json.loads(
        (tmp_path / matrix["tasks"][0]["manifest"]).read_text(encoding="utf-8")
    )
    assert manifest["module"] == "routeA.generate_degree24_pair_resolvents"
    assert manifest["args"][manifest["args"].index("--source-t") + 1] == "2"
    assert manifest["campaign"]["certificate"] == "server-source-plus-gap-exact-pair-action"
    assert all(artifact["required"] for artifact in manifest["artifacts"])
    assert "IGP24_API_KEY" not in json.dumps(manifest)


def test_pair_resolvent_plan_can_keep_multiple_closures_per_target(tmp_path):
    catalog = tmp_path / "routeA" / "data" / "verified.jsonl"
    orbit_map = tmp_path / "routeA" / "data" / "pair-map.jsonl"
    _write_jsonl(catalog, [
        {
            "source_t": source_t,
            "source_r": 0,
            "coefficients": [source_t] + [0] * 23 + [1],
            "disc_abs": source_t,
            "candidate_hash": f"hash-{source_t}",
        }
        for source_t in (2, 3)
    ])
    _write_jsonl(orbit_map, [
        {
            "source_t": source_t,
            "orbit_targets": [10],
            "orbit_count": 1,
            "unambiguous_target_t": 10,
        }
        for source_t in (2, 3)
    ])
    output = tmp_path / "cloud" / "campaigns" / "pair-full"
    summary = plan_pair_resolvent_campaign(
        catalog,
        orbit_map,
        campaign_id="pair-full",
        output_dir=output,
        db_path=None,
        sources_per_target=2,
        project=tmp_path,
    )
    assert summary["task_count"] == 2
    assert summary["target_count"] == 1
    assert summary["live_opportunity"] == 1.0


def test_pair_resolvent_plan_can_shard_multiple_fields_of_one_source_group(tmp_path):
    catalog = tmp_path / "routeA" / "data" / "verified.jsonl"
    orbit_map = tmp_path / "routeA" / "data" / "pair-map.jsonl"
    _write_jsonl(catalog, [
        {
            "source_t": 2,
            "source_r": root,
            "coefficients": [root + 1] + [0] * 23 + [1],
            "disc_abs": root + 10,
            "candidate_hash": f"hash-{root}",
        }
        for root in (0, 4)
    ])
    _write_jsonl(orbit_map, [{
        "source_t": 2,
        "orbit_targets": [10],
        "orbit_count": 1,
        "unambiguous_target_t": 10,
    }])
    output = tmp_path / "cloud" / "campaigns" / "pair-fields"
    summary = plan_pair_resolvent_campaign(
        catalog,
        orbit_map,
        campaign_id="pair-fields",
        output_dir=output,
        db_path=None,
        sources_per_target=2,
        fields_per_source=2,
        project=tmp_path,
    )
    assert summary["source_count"] == 1
    assert summary["source_field_count"] == 2
    matrix = json.loads((output / "matrix.json").read_text())
    assert len({task["job_id"] for task in matrix["tasks"]}) == 2


def test_pair_resolvent_plan_can_include_exact_self_maps(tmp_path):
    catalog = tmp_path / "routeA" / "data" / "verified.jsonl"
    orbit_map = tmp_path / "routeA" / "data" / "pair-map.jsonl"
    _write_jsonl(catalog, [{
        "source_t": 3,
        "source_r": 0,
        "coefficients": [3] + [0] * 23 + [1],
        "disc_abs": 25,
        "candidate_hash": "hash-3",
    }])
    _write_jsonl(orbit_map, [{
        "source_t": 3,
        "orbit_targets": [3],
        "orbit_count": 1,
        "unambiguous_target_t": 3,
    }])
    output = tmp_path / "cloud" / "campaigns" / "pair-self"
    summary = plan_pair_resolvent_campaign(
        catalog,
        orbit_map,
        campaign_id="pair-self",
        output_dir=output,
        db_path=None,
        include_self_maps=True,
        project=tmp_path,
    )
    assert summary["task_count"] == 1
    assert summary["rejected_self_maps"] == 0
    assert summary["include_self_maps"] is True
    matrix = json.loads((output / "matrix.json").read_text())
    manifest = json.loads(
        (tmp_path / matrix["tasks"][0]["manifest"]).read_text()
    )
    assert manifest["args"][-1] == "--allow-self-map"


def test_pair_resolvent_plan_can_emit_ordered_difference_jobs(tmp_path):
    catalog = tmp_path / "routeA" / "data" / "verified.jsonl"
    orbit_map = tmp_path / "routeA" / "data" / "ordered-map.jsonl"
    _write_jsonl(catalog, [{
        "source_t": 3,
        "source_r": 0,
        "coefficients": [3] + [0] * 23 + [1],
        "disc_abs": 25,
        "candidate_hash": "hash-3",
    }])
    _write_jsonl(orbit_map, [{
        "source_t": 3,
        "orbit_targets": [3, 3],
        "orbit_count": 2,
        "unambiguous_target_t": 3,
    }])
    output = tmp_path / "cloud" / "campaigns" / "ordered"
    summary = plan_pair_resolvent_campaign(
        catalog,
        orbit_map,
        campaign_id="ordered",
        output_dir=output,
        db_path=None,
        include_self_maps=True,
        action_kind="ordered-pair",
        project=tmp_path,
    )
    assert summary["action_kind"] == "ordered-pair"
    matrix = json.loads((output / "matrix.json").read_text())
    manifest = json.loads((tmp_path / matrix["tasks"][0]["manifest"]).read_text())
    assert manifest["args"][-2:] == ["--resolvent-kind", "ordered-affine"]
    assert manifest["campaign"]["certificate"].endswith("ordered-pair-action")


def test_pair_resolvent_plan_can_emit_generic_unordered_jobs(tmp_path):
    catalog = tmp_path / "routeA" / "data" / "verified.jsonl"
    orbit_map = tmp_path / "routeA" / "data" / "pair-map.jsonl"
    _write_jsonl(catalog, [{
        "source_t": 3,
        "source_r": 0,
        "coefficients": [3] + [0] * 23 + [1],
        "disc_abs": 25,
        "candidate_hash": "hash-3",
    }])
    _write_jsonl(orbit_map, [{
        "source_t": 3,
        "orbit_targets": [10],
        "orbit_count": 1,
        "unambiguous_target_t": 10,
    }])
    output = tmp_path / "cloud" / "campaigns" / "generic"
    summary = plan_pair_resolvent_campaign(
        catalog,
        orbit_map,
        campaign_id="generic",
        output_dir=output,
        db_path=None,
        action_kind="generic-unordered-pair",
        product_weight=3,
        project=tmp_path,
    )
    assert summary["action_kind"] == "generic-unordered-pair"
    matrix = json.loads((output / "matrix.json").read_text())
    assert matrix["mode"] == "exact-degree24-generic-pair-resolvent"
    assert matrix["product_weight"] == 3
    assert matrix["tasks"][0]["lane"] == "generic-pair-resolvent"
    manifest = json.loads((tmp_path / matrix["tasks"][0]["manifest"]).read_text())
    assert manifest["args"][-4:] == [
        "--resolvent-kind", "pair-sum-product", "--product-weight", "3",
    ]


def test_pair_resolvent_plan_emits_ambiguous_joint_classification_jobs(tmp_path):
    catalog = tmp_path / "routeA" / "data" / "verified.jsonl"
    orbit_map = tmp_path / "routeA" / "data" / "pair-map.jsonl"
    profiles = tmp_path / "routeA" / "data" / "joint.jsonl"
    _write_jsonl(catalog, [{
        "source_t": 164,
        "source_r": 0,
        "coefficients": [3] + [0] * 23 + [1],
        "disc_abs": 25,
        "candidate_hash": "hash-164",
    }])
    _write_jsonl(orbit_map, [{
        "source_t": 164,
        "orbit_targets": [163, 164],
        "orbit_count": 2,
        "unambiguous_target_t": None,
    }])
    _write_jsonl(profiles, [{
        "source_t": 164,
        "group_order": 2,
        "orbit_count": 2,
        "orbit_targets": [163, 164],
        "profiles": [{
            "class_size": 2,
            "source_cycle": [24],
            "orbit_cycles": [[24], [12, 12]],
        }],
    }])
    output = tmp_path / "cloud" / "campaigns" / "ambiguous"
    summary = plan_pair_resolvent_campaign(
        catalog,
        orbit_map,
        campaign_id="ambiguous",
        output_dir=output,
        db_path=None,
        action_kind="ambiguous-unordered-pair",
        joint_profile_path=profiles,
        classification_prime_count=146,
        minimum_good_primes=16,
        minimum_classification_confidence=0.99,
        project=tmp_path,
    )
    assert summary["task_count"] == 1
    assert summary["source_count"] == 1
    assert summary["target_count"] == 2
    assert summary["live_opportunity"] == 2.0
    matrix = json.loads((output / "matrix.json").read_text())
    assert matrix["mode"] == "exact-degree24-ambiguous-pair-resolvent"
    manifest = json.loads((tmp_path / matrix["tasks"][0]["manifest"]).read_text())
    assert manifest["campaign"]["target_ts"] == [163, 164]
    assert "pair-sum-product" in manifest["args"]
    assert "--joint-profile" in manifest["args"]
    assert manifest["campaign"]["certificate"].endswith(
        "coupled-frobenius-classification"
    )
    assert "IGP24_API_KEY" not in json.dumps(manifest)
