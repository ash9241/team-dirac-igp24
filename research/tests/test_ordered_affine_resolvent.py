from routeA.ordered_affine_resolvent import canonical_monic


def test_canonical_monic_keeps_degree132_shape():
    values = [0, 0, 1] + [0] * 130 + [1]
    assert canonical_monic(values).split(",")[-1] == "1"
