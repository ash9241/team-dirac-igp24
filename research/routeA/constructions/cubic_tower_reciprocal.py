"""Cubic-fiber reciprocal twists above ``12T274``.

The cubic

    q(y) = y^3 + 3m y^2 - (12 + 12m)y - (16 + 36m)

has ``q(-2)=0`` and ``q'(2)=0``.  Its other derivative root is
``rho=-2m-2``.  For

    f(y) = (q(y)^2 + h)^2 - d,

the critical values above the two unaccounted roots of ``q=0`` occur in a
square pair.  Consequently the reciprocal character is controlled by the
    single value at ``rho``.  This gives direct integer parameterizations of the
    product and within characters of the generic ``12T274`` quotient.  A third
    parameterization identifies the endpoint character with the aggregate
    cubic-fiber sign and reaches ``24T24151``.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from math import isqrt
from typing import Any

from routeA.constructions.compositum_8x3 import DEFAULT_GP, _run_gp, polynomial_expression
from routeA.constructions.twisted_reciprocal import _convolve, _evaluate, reciprocal_lift
from routeA.ledger import candidate_hash, canonical_coefficients


TARGETS = {
    "even": 24150,
    "fiber": 24151,
    "d": 24152,
    "d_fiber": 24153,
    "d_outer": 24154,
    "product": 24155,
    "outer": 24156,
    "within": 24157,
}


@dataclass(frozen=True)
class CubicTowerReciprocalSpec:
    character: str
    m: int
    s: int
    control: int
    h: int
    d: int
    critical_root: int
    critical_q_value: int
    target_t: int
    cubic_coefficients: tuple[int, ...]
    base_coefficients: tuple[int, ...]
    coefficients: tuple[int, ...]

    @property
    def line(self) -> str:
        return canonical_coefficients(self.coefficients)

    @property
    def parameters(self) -> dict[str, Any]:
        if self.character == "fiber":
            return {"m": self.m, "h": self.h, "d": self.d}
        key = "k" if self.character == "product" else "W"
        return {"m": self.m, "s": self.s, key: self.control, "h": self.h, "d": self.d}


@dataclass(frozen=True)
class CubicTowerReciprocalCandidate:
    coefficients: str
    candidate_hash: str
    local_root_count: int
    local_irreducible: bool
    target_t: int
    target_r: int
    recipe_family: str
    recipe_id: str
    construction_overgroup: str
    parameters: dict[str, Any]
    cubic_coefficients: tuple[int, ...]
    base_coefficients: tuple[int, ...]
    base_discriminant_squareclass: int
    endpoint_product_squareclass: int
    character_identity_verified: bool
    outer_group_order: int
    field_disc_abs: int
    cpu_ms: float

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["cubic_coefficients"] = list(self.cubic_coefficients)
        value["base_coefficients"] = list(self.base_coefficients)
        return value


def _cubic(m: int) -> tuple[int, ...]:
    m = int(m)
    return (-16 - 36 * m, -12 - 12 * m, 3 * m, 1)


def build_product_cubic_tower(m: int, s: int, k: int) -> CubicTowerReciprocalSpec:
    """Tie reciprocal flip parity to top times discriminant."""

    m, s, k = int(m), int(s), int(k)
    if m in {0, -2} or s <= 0 or k == 0:
        raise ValueError("require m not in {0,-2}, s positive, and k nonzero")
    q = _cubic(m)
    rho = -2 * m - 2
    q_rho = _evaluate(q, rho)
    weight = 1 + s * s
    h = weight * k - q_rho * q_rho
    d = weight * k * k
    return _build_spec("product", m, s, k, h, d, q, rho, q_rho)


def build_within_cubic_tower(m: int, s: int, W: int) -> CubicTowerReciprocalSpec:
    """Tie reciprocal flip parity to the degree-12 discriminant."""

    m, s, W = int(m), int(s), int(W)
    if m in {0, -2} or s <= 0 or abs(W) <= s:
        raise ValueError("require m not in {0,-2}, s positive, and |W| > s")
    q = _cubic(m)
    rho = -2 * m - 2
    q_rho = _evaluate(q, rho)
    h = W - q_rho * q_rho
    d = W * W - s * s
    return _build_spec("within", m, s, W, h, d, q, rho, q_rho)


def build_fiber_cubic_tower(m: int, d: int) -> CubicTowerReciprocalSpec:
    """Tie reciprocal flip parity to the aggregate cubic-fiber sign.

    Put ``rho=-2m-2`` and ``R=q(rho)^2``.  The choice ``h=-R/2``
    makes ``f(-2)=f(rho)``.  Therefore the endpoint square class
    ``f(-2)f(2)`` equals the cubic-fiber character ``f(rho)f(2)``.
    GAP identifies this generic reciprocal twist as ``24T24151``.
    """

    m, d = int(m), int(d)
    if m in {0, -2}:
        raise ValueError("require m not in {0,-2}")
    if d <= 0 or isqrt(d) ** 2 == d:
        raise ValueError("d must be a positive nonsquare")
    q = _cubic(m)
    rho = -2 * m - 2
    q_rho = _evaluate(q, rho)
    h = -(q_rho * q_rho) // 2
    return _build_spec("fiber", m, 0, d, h, d, q, rho, q_rho)


def _build_spec(
    character: str,
    m: int,
    s: int,
    control: int,
    h: int,
    d: int,
    q: tuple[int, ...],
    rho: int,
    q_rho: int,
) -> CubicTowerReciprocalSpec:
    if d <= 0 or isqrt(d) ** 2 == d:
        raise ValueError("d must be a positive nonsquare")
    q_squared = _convolve(q, q)
    inner = q_squared[:]
    inner[0] += h
    base = _convolve(inner, inner)
    base[0] -= d
    critical_value = _evaluate(base, rho)
    if character == "product":
        valid = critical_value == d * s * s
    elif character == "within":
        valid = critical_value == s * s
    elif character == "fiber":
        valid = _evaluate(base, -2) == critical_value
    else:
        raise ValueError(f"unsupported cubic-tower character {character!r}")
    if not valid:
        raise AssertionError("mixed critical-point identity failed")
    return CubicTowerReciprocalSpec(
        character=character,
        m=m,
        s=s,
        control=control,
        h=h,
        d=d,
        critical_root=rho,
        critical_q_value=q_rho,
        target_t=TARGETS[character],
        cubic_coefficients=q,
        base_coefficients=tuple(base),
        coefficients=reciprocal_lift(base),
    )


def certify_cubic_tower(
    spec: CubicTowerReciprocalSpec,
    *,
    expected_root_count: int | None = None,
    gp: str = DEFAULT_GP,
    timeout: float = 1200,
) -> CubicTowerReciprocalCandidate:
    """Check local arithmetic and the character identity in PARI/GP."""

    f = polynomial_expression(spec.base_coefficients, "x")
    p = polynomial_expression(spec.coefficients, "x")
    outer = f"x^4+({2 * spec.h})*x^2+({spec.h * spec.h - spec.d})"
    started = time.perf_counter()
    script = f"""
default(new_galois_format,1); o={outer}; f={f}; p={p};
og=polgalois(o);
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(!polisirreducible(p),print("ERROR|lift-reducible");quit(1));
print("OUTER_ORDER|",og[1]); print("ROOTS|",polsturm(p));
print("DISC_CORE|",core(poldisc(f)));
print("END_CORE|",core(subst(f,x,2)*subst(f,x,-2)));
print("NFDISC|",abs(nfdisc(p))); quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    values = {
        tag: value
        for line in output.splitlines()
        if "|" in line
        for tag, value in [line.strip().split("|", 1)]
        if tag in {
            "ERROR", "OUTER_ORDER", "ROOTS", "DISC_CORE", "END_CORE", "NFDISC"
        }
    }
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    roots = int(values["ROOTS"])
    if expected_root_count is not None and roots != int(expected_root_count):
        raise ValueError(f"signature mismatch: expected {expected_root_count}, got {roots}")
    outer_order = int(values["OUTER_ORDER"])
    if outer_order != 8:
        raise ValueError(f"outer quartic is not generic D4 (order={outer_order})")
    disc_core, endpoint_core = int(values["DISC_CORE"]), int(values["END_CORE"])
    if spec.character == "within":
        identity = disc_core == endpoint_core
    elif spec.character == "product":
        identity = _is_square(
            _evaluate(spec.base_coefficients, 2)
            * _evaluate(spec.base_coefficients, -2)
            * spec.d
            * disc_core
        )
    elif spec.character == "fiber":
        identity = _evaluate(spec.base_coefficients, -2) == _evaluate(
            spec.base_coefficients, spec.critical_root
        )
    else:
        raise ValueError(f"unsupported cubic-tower character {spec.character!r}")
    if not identity:
        raise AssertionError("engineered reciprocal character identity failed")
    line = spec.line
    parameter_key = ":".join(
        f"{key}={value}" for key, value in sorted(spec.parameters.items())
    )
    return CubicTowerReciprocalCandidate(
        coefficients=line,
        candidate_hash=candidate_hash(line),
        local_root_count=roots,
        local_irreducible=True,
        target_t=spec.target_t,
        target_r=roots,
        recipe_family="cubic_tower_reciprocal_12T274",
        recipe_id=f"cubic-tower-reciprocal:{spec.character}:{parameter_key}",
        construction_overgroup="C2^11_character_twist_over_12T274",
        parameters={**spec.parameters, "character": spec.character},
        cubic_coefficients=spec.cubic_coefficients,
        base_coefficients=spec.base_coefficients,
        base_discriminant_squareclass=disc_core,
        endpoint_product_squareclass=endpoint_core,
        character_identity_verified=True,
        outer_group_order=outer_order,
        field_disc_abs=int(values["NFDISC"]),
        cpu_ms=(time.perf_counter() - started) * 1000.0,
    )


def _is_square(value: int) -> bool:
    value = int(value)
    return value >= 0 and isqrt(value) ** 2 == value
