from __future__ import annotations

import json

from routeA.build_catalog_character_campaign import (
    DEFAULT_CATALOG,
    DEFAULT_MAP,
    character_targets,
    load_jsonl,
    ranked_catalog_families,
)


def test_full_degree12_catalog_and_character_map_are_available() -> None:
    catalog = load_jsonl(DEFAULT_CATALOG)
    assert len(catalog) == 301
    assert {int(row["base_t"]) for row in catalog} == set(range(1, 302))

    targets = character_targets(load_jsonl(DEFAULT_MAP))
    assert targets[77] == {"sign": 19322, "trivial": 19322}
    assert targets[156] == {"sign": 21376, "trivial": 21370}
    assert targets[288] == {"sign": 24492, "trivial": 24493}


def test_catalog_sign_families_are_score_ranked() -> None:
    families = ranked_catalog_families(character="sign", owned_pairs=set())
    assert families
    assert all(
        left.live_ceiling >= right.live_ceiling
        for left, right in zip(families, families[1:])
    )
    base288 = next(item.family for item in families if item.family.base_t == 288)
    assert base288.target_t == 24492
    assert base288.expected_norm_squareclass == 10351121
    assert base288.seed_coefficients == (
        15062247,
        30753556,
        16932087,
        -1532416,
        -3728245,
        -654228,
        189133,
        54688,
        -2664,
        -1410,
        0,
        12,
    )


def test_repeated_field_catalog_keeps_distinct_arithmetic_seeds(tmp_path) -> None:
    source = next(row for row in load_jsonl(DEFAULT_CATALOG) if int(row["base_t"]) == 105)
    alternate = dict(source)
    alternate["label"] = str(source["label"]) + ".alternate"
    catalog = tmp_path / "alternates.jsonl"
    catalog.write_text(
        json.dumps(source) + "\n" + json.dumps(alternate) + "\n",
        encoding="utf-8",
    )

    families = ranked_catalog_families(
        catalog_path=catalog,
        character="sign",
        owned_pairs=set(),
        allow_repeated_fields=True,
    )
    selected = [item for item in families if item.family.base_t == 105]
    assert len(selected) == 2
    assert len({item.family.family for item in selected}) == 2
