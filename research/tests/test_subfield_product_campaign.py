import json
from fractions import Fraction

import routeA.build_subfield_product_campaign as product


def _row(degree, index, shift, h, norm=1):
    return {
        "base_t": 1,
        "base_coefficients": [1] + [0] * 11 + [1],
        "h_coefficients": h,
        "norm_squareclass": norm,
        "candidate_hash": f"{degree}-{index}-{shift}",
        "parameters": {
            "subfield_degree": degree,
            "subfield_index": index,
            "subfield_shift": shift,
        },
    }


def test_enumerate_products_requires_distinct_subfield_structures():
    rows = [
        _row(2, 1, 0, [0, 1], 2),
        _row(2, 1, 1, [-1, 1], 3),
        _row(3, 1, 0, [1, 1], 5),
    ]
    seeds = product.enumerate_product_seeds(rows, max_products=None)
    assert len(seeds) == 2
    assert {seed["norm_squareclass"] for seed in seeds} == {10, 15}
    assert all(len(seed["factors"]) == 2 for seed in seeds)
    assert {tuple(seed["seed_coefficients"]) for seed in seeds} == {
        (Fraction(0), Fraction(1), Fraction(1)),
        (Fraction(-1), Fraction(0), Fraction(1)),
    }


def test_build_product_lifts_parses_certificates(monkeypatch):
    polynomial = [1] + [0] * 23 + [1]
    output = f"CAND|1|4|10|12345|{polynomial}\n"
    monkeypatch.setattr(product, "_run_gp", lambda *args, **kwargs: output)
    rows, counts = product.build_product_lifts(
        [1] + [0] * 11 + [1],
        [{
            "seed_coefficients": (Fraction(1), Fraction(1)),
            "norm_squareclass": 10,
            "factors": [],
        }],
    )
    assert counts["validity_lifts"] == 1
    assert rows[0]["target_r"] == 4
    assert rows[0]["field_disc_abs"] == 12345
    assert rows[0]["coefficients"] == tuple(polynomial)


def test_worker_allowlists_subfield_product_module():
    from cloud.generator_worker import ALLOWED_MODULES

    assert "routeA.build_subfield_product_campaign" in ALLOWED_MODULES
