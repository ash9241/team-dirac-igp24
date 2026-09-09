import json

from cloud.plan_character_closure_campaign import plan_closure_campaign


def test_plan_closure_campaign_keeps_field_generators_together(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    map_path = project / "map.jsonl"
    map_path.write_text(
        "{\"base_t\": 9, \"target_t\": 90}\n"
        "{\"base_t\": 9, \"target_t\": 91}\n"
        "{\"base_t\": 9, \"target_t\": 92}\n",
        encoding="utf-8",
    )
    discoveries = project / "discoveries.jsonl"
    rows = [
        {
            "base_t": 9,
            "label": "2.2.5.1",
            "disc_abs": 20,
            "base_coefficients": [-5, 0, 1],
            "seed_coefficients": ["1/2", "1/2"],
            "norm_squareclass": 2,
        }
    ]
    discoveries.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    output = project / "campaign"
    report = plan_closure_campaign(
        discoveries,
        output,
        campaign_id="closure-test",
        map_path=map_path,
        project=project,
    )
    assert report["task_count"] == 1
    assert report["candidate_seeds"] == 1
    matrix = json.loads((output / "matrix.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (project / matrix["tasks"][0]["manifest"]).read_text(encoding="utf-8")
    )
    assert manifest["module"] == "routeA.discover_character_closure"
    assert manifest["campaign"]["source_rows"] == 1
    assert manifest["campaign"]["candidate_seeds"] == 1
