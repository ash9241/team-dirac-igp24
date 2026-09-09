from routeA.discover_linear_characters import candidate_linear_seeds


def test_linear_character_prefilter_finds_known_nontrivial_norms() -> None:
    seeds = candidate_linear_seeds(shift_min=-5, shift_max=5)
    triples = {
        (int(row["base_t"]), int(row["shift"]), int(row["norm_squareclass"]))
        for row in seeds
    }
    assert (52, 0, 2) in triples
    assert (136, 1, 5) in triples
    assert (209, -1, 5) in triples
    assert all(int(row["norm_squareclass"]) > 1 for row in seeds)
