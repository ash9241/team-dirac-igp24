import json

import pytest

from cloud.export_standard_character_discoveries import (
    export_standard_discoveries,
    standard_character_discoveries,
)


def test_standard_trivial_export_covers_every_degree12_group(tmp_path) -> None:
    rows = standard_character_discoveries(character="trivial")
    assert len(rows) == 301
    assert {row["base_t"] for row in rows} == set(range(1, 302))
    base288 = next(row for row in rows if row["base_t"] == 288)
    assert base288["starting_target_t"] == 24493
    assert base288["norm_squareclass"] == 1
    assert base288["seed_coefficients"] == [1]
    assert base288["server_calibrated"] is False
    report = export_standard_discoveries(
        tmp_path / "trivial.jsonl",
        character="trivial",
    )
    assert report["rows"] == 301
    assert report["targets"] == 301
    assert report["characters"] == ["trivial"]


def test_repeated_field_export_can_exclude_fields_already_in_candidates(tmp_path) -> None:
    catalog = tmp_path / "alternates.jsonl"
    fields = [
        {
            "base_t": 1,
            "coefficients": [offset] + [0] * 11 + [1],
            "disc_abs": 10 + offset,
            "label": f"alternate-{offset}",
        }
        for offset in (1, 2)
    ]
    catalog.write_text(
        "".join(json.dumps(row) + "\n" for row in fields),
        encoding="utf-8",
    )
    candidates = tmp_path / "prior.jsonl"
    candidates.write_text(json.dumps({
        "parameters": {"base_t": 1},
        "base_coefficients": fields[0]["coefficients"],
    }) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected 301"):
        standard_character_discoveries(catalog_path=catalog)

    rows = standard_character_discoveries(
        catalog_path=catalog,
        character="both",
        allow_repeated_fields=True,
        exclude_candidate_paths=[candidates],
    )
    assert len(rows) == 2
    assert {row["standard_character"] for row in rows} == {"sign", "trivial"}
    assert {tuple(row["base_coefficients"]) for row in rows} == {
        tuple(fields[1]["coefficients"]),
    }
