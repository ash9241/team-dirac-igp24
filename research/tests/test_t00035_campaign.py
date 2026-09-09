from routeA.build_t00035_campaign import campaign_families


def test_t00035_campaign_has_unique_exact_rows() -> None:
    families = campaign_families()
    pairs = {
        (family.target_t, root)
        for family in families
        for root in family.target_root_counts
    }
    assert len(families) == 65
    assert len(pairs) == 425
    assert sum(len(family.target_root_counts) for family in families) == len(pairs)
    assert all(len(family.base_coefficients) == 13 for family in families)
    assert all(family.base_coefficients[-1] == 1 for family in families)


def test_t00035_campaign_contains_high_value_character_targets() -> None:
    targets = {family.target_t for family in campaign_families()}
    assert {
        20525,
        21570,
        21571,
        22404,
        22808,
        23115,
        23797,
        24075,
        24501,
        24561,
        22756,
        24971,
    } <= targets
