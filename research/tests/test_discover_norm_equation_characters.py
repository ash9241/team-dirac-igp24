from __future__ import annotations

from fractions import Fraction

import routeA.discover_norm_equation_characters as norm_discovery
from routeA.discover_norm_equation_characters import (
    _parse_norm_rows,
    solve_supported_norms,
    supported_squareclasses,
)


def test_supported_squareclasses_omit_trivial_and_sign() -> None:
    # core(2^3 * 3^2 * 5) = 10, so 10 is the sign character.
    values = supported_squareclasses(2**3 * 3**2 * 5)
    assert 1 not in values
    assert -1 in values
    assert 10 not in values
    assert -10 in values
    assert {2, -2, 3, -3, 5, -5, 6, -6, 15, -15}.issubset(values)


def test_parse_norm_rows_preserves_rational_power_basis() -> None:
    output = "NORM|-5|11|17|[1, -2, 0]|[3, 5, 1]\n"
    assert _parse_norm_rows(output) == [{
        "norm_squareclass": -5,
        "norm_scale": 11,
        "height": 17,
        "seed_coefficients": [Fraction(1, 3), Fraction(-2, 5)],
    }]


def test_norm_solver_timeboxes_initialization_and_each_equation(monkeypatch) -> None:
    observed = {}

    def fake_gp(script, *, gp, timeout):
        observed.update(script=script, gp=gp, timeout=timeout)
        return ""

    monkeypatch.setattr(norm_discovery, "_run_gp", fake_gp)
    rows = solve_supported_norms(
        {"coefficients": [1] + [0] * 11 + [1]},
        [2, -2],
        scales=[67],
        equation_timeout=3,
        initialization_timeout=7,
        timeout=11,
        gp="gp-test",
    )
    assert rows == []
    assert "alarm(7,bnfinit(f,1))" in observed["script"]
    assert "alarm(3,bnfisintnorm(b,n))" in observed["script"]
    assert observed["gp"] == "gp-test"
    assert observed["timeout"] == 11
