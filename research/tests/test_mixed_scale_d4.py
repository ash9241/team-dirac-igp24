from routeA.mixed_scale_d4 import generate


def test_mixed_scale_generator_reaches_twenty_real_roots():
    rows = generate(attempts=100, limit=3, seed=1898620, coefficient_bound=1)
    assert len(rows) == 3
    assert all(row["local_root_count"] == 20 for row in rows)
    assert all(row["quotient_evidence"]["expected_quotient6"] == 6 for row in rows)
    assert all(18986 in row["compatible_labels"] for row in rows)
