from routeA.generate_pair_resolvent_catalog import (
    pair_sum_resolvent,
    parse_pair_orbits,
)


def test_pair_sum_resolvent_on_split_cubic() -> None:
    # Roots 1,2,4 give pair sums 3,5,6.
    source = (-8, 14, -7, 1)
    assert pair_sum_resolvent(source) == (-90, 63, -14, 1)


def test_parse_pair_orbits_retains_multiplicity() -> None:
    rows = parse_pair_orbits([
        "noise",
        "PAIR12|49|50,",
        "PAIR12|50|49,52,49,",
    ])
    assert rows[49] == (50,)
    assert rows[50] == (49, 52, 49)
