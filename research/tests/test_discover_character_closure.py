from fractions import Fraction

from routeA.discover_character_closure import _multiply_mod, character_closure_seeds


def test_multiply_mod_reduces_monic_polynomials() -> None:
    # x * x = -1 modulo x^2 + 1.
    assert _multiply_mod((0, 1), (0, 1), (1, 0, 1)) == (-1,)


def test_multiply_mod_preserves_rational_power_basis_coordinates() -> None:
    # ((1+x)/2) * x = (5+x)/2 modulo x^2-5.
    assert _multiply_mod(("1/2", "1/2"), (0, 1), (-5, 0, 1)) == (
        Fraction(5, 2),
        Fraction(1, 2),
    )


def test_closure_toggles_permutation_sign(tmp_path) -> None:
    map_path = tmp_path / "map.jsonl"
    map_path.write_text(
        "{\"base_t\": 9, \"target_t\": 90}\n"
        "{\"base_t\": 9, \"target_t\": 91}\n"
        "{\"base_t\": 9, \"target_t\": 92}\n",
        encoding="utf-8",
    )
    # f=x^2-5 has discriminant 20 and squareclass 5.  A calibrated class 2
    # therefore
    # yields the unseen product class 10 after multiplying by f'.
    rows = [{
        "base_t": 9,
        "label": "2.2.5.1",
        "disc_abs": 20,
        "base_coefficients": [-5, 0, 1],
        "shift": 0,
        "norm_squareclass": 2,
    }]
    seeds = character_closure_seeds(rows, map_path=map_path)
    assert len(seeds) == 1
    assert seeds[0]["norm_squareclass"] == 10
    assert seeds[0]["closure_includes_permutation_sign"] is True


def test_closure_accepts_rational_integral_basis_seeds(tmp_path) -> None:
    map_path = tmp_path / "map.jsonl"
    map_path.write_text(
        "{\"base_t\": 9, \"target_t\": 90}\n"
        "{\"base_t\": 9, \"target_t\": 91}\n"
        "{\"base_t\": 9, \"target_t\": 92}\n",
        encoding="utf-8",
    )
    rows = [{
        "base_t": 9,
        "label": "2.2.5.1",
        "disc_abs": 20,
        "base_coefficients": [-5, 0, 1],
        "seed_coefficients": ["1/2", "1/2"],
        "norm_squareclass": 2,
    }]
    seeds = character_closure_seeds(rows, map_path=map_path)
    assert len(seeds) == 1
    assert seeds[0]["norm_squareclass"] == 10
    assert seeds[0]["seed_coefficients"] == [5, 1]


def test_closure_retains_distinct_characters_with_same_rational_norm(tmp_path) -> None:
    map_path = tmp_path / "map.jsonl"
    map_path.write_text(
        "{\"base_t\": 9, \"target_t\": 90}\n"
        "{\"base_t\": 9, \"target_t\": 91}\n"
        "{\"base_t\": 9, \"target_t\": 92}\n",
        encoding="utf-8",
    )
    rows = [
        {
            "base_t": 9,
            "starting_target_t": target,
            "label": "2.2.5.1",
            "disc_abs": 20,
            "base_coefficients": [-5, 0, 1],
            "seed_coefficients": seed,
            "norm_squareclass": 2,
        }
        for target, seed in ((90, [0, 1]), (91, [1, 1]))
    ]
    seeds = character_closure_seeds(rows, map_path=map_path)
    square_norm_products = [
        row for row in seeds
        if row["norm_squareclass"] == 1
        and not row["closure_includes_permutation_sign"]
    ]
    assert len(square_norm_products) == 1
    assert square_norm_products[0]["closure_source_characters"] == [
        {"starting_target_t": 90, "norm_squareclass": 2},
        {"starting_target_t": 91, "norm_squareclass": 2},
    ]
