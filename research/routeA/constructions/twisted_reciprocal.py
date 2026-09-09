"""Character-engineered reciprocal lifts targeting rare degree-24 groups.

Let ``f(y) = g(y)^2 - 5`` and ``P(x) = x^12 f(x + x^-1)``.  The base
polynomial generically has group ``12T299 = S6 wr C2``.  We force the
square class controlling the reciprocal flip kernel by taking

    g'(y) = 6 (y^2 - 4) (y - c) (y - r)^2.

Modulo squares, ``Disc(f) = f(2) f(-2) f(c)``.  Setting ``g(c)=3`` makes
``f(c)=4`` and targets the within-block character (``24T24871``); setting
``g(c)=5`` makes ``f(c)=20`` and targets the product character
(``24T24869``).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from routeA.constructions.compositum_8x3 import (
    DEFAULT_GP,
    _run_gp,
    polynomial_expression,
)
from routeA.ledger import candidate_hash, canonical_coefficients


TWIST_PARAMETERS = {
    "within": {"critical_value": 3, "target_t": 24871},
    "product": {"critical_value": 5, "target_t": 24869},
}


@dataclass(frozen=True)
class TwistedReciprocalSpec:
    character: str
    c: int
    r: int
    d: int
    target_t: int
    inner_coefficients: tuple[int, ...]
    base_coefficients: tuple[int, ...]
    coefficients: tuple[int, ...]

    @property
    def line(self) -> str:
        return canonical_coefficients(self.coefficients)

    @property
    def parameters(self) -> dict[str, Any]:
        return {"portrait": "double", "c": self.c, "r": self.r, "d": self.d}


@dataclass(frozen=True)
class CriticalTwistedReciprocalSpec:
    character: str
    critical_roots: tuple[int, int, int]
    marked_root: int
    critical_value: int
    d: int
    target_t: int
    inner_coefficients: tuple[int, ...]
    base_coefficients: tuple[int, ...]
    coefficients: tuple[int, ...]

    @property
    def line(self) -> str:
        return canonical_coefficients(self.coefficients)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "portrait": "five-simple",
            "critical_roots": list(self.critical_roots),
            "marked_root": self.marked_root,
            "critical_value": self.critical_value,
            "d": self.d,
        }


@dataclass(frozen=True)
class TwistedReciprocalCandidate:
    coefficients: str
    candidate_hash: str
    local_root_count: int
    local_irreducible: bool
    target_t: int
    target_r: int
    label_probability: float
    recipe_family: str
    recipe_lineage: str
    recipe_id: str
    construction_overgroup: str
    parameters: dict[str, Any]
    inner_coefficients: tuple[int, ...]
    base_coefficients: tuple[int, ...]
    base_discriminant_squareclass: int
    endpoint_product_squareclass: int
    character_identity_verified: bool
    field_disc_abs: int
    cpu_ms: float

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["inner_coefficients"] = list(self.inner_coefficients)
        value["base_coefficients"] = list(self.base_coefficients)
        return value


def _convolve(left: Sequence[int], right: Sequence[int]) -> list[int]:
    out = [0] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            out[i + j] += int(a) * int(b)
    return out


def _evaluate(coefficients: Sequence[int], value: int) -> int:
    result = 0
    for coefficient in reversed(coefficients):
        result = result * int(value) + int(coefficient)
    return result


def critical_inner_coefficients(c: int, r: int, critical_value: int) -> tuple[int, ...]:
    """Build a monic integral ``g`` with the required critical portrait."""

    c, r, critical_value = int(c), int(r), int(critical_value)
    if r % 2 or (c + 2 * r) % 5:
        raise ValueError("integrality requires r even and c + 2r divisible by 5")
    if len({c, r, -2, 2}) != 4:
        raise ValueError("c, r, -2, and 2 must be distinct")

    # Ascending coefficients of (y^2-4)(y-c)(y-r)^2.
    derivative_factor = _convolve(
        _convolve((-4, 0, 1), (-c, 1)),
        (r * r, -2 * r, 1),
    )
    coefficients = [0]
    for power, coefficient in enumerate(derivative_factor):
        numerator = 6 * coefficient
        denominator = power + 1
        if numerator % denominator:
            raise AssertionError("integrality congruences were insufficient")
        coefficients.append(numerator // denominator)
    coefficients[0] = critical_value - _evaluate(coefficients, c)
    if len(coefficients) != 7 or coefficients[-1] != 1:
        raise AssertionError("inner polynomial is not monic of degree six")
    return tuple(coefficients)


def reciprocal_lift(base_coefficients: Sequence[int]) -> tuple[int, ...]:
    """Return ascending coefficients of ``x^12 f(x + x^-1)``."""

    from math import comb

    base = tuple(int(value) for value in base_coefficients)
    if len(base) != 13 or base[-1] != 1:
        raise ValueError("base polynomial must be monic of degree twelve")
    output = [0] * 25
    for power, coefficient in enumerate(base):
        for j in range(power + 1):
            output[12 + power - 2 * j] += coefficient * comb(power, j)
    if output[-1] != 1 or output[0] != 1:
        raise AssertionError("reciprocal lift lost monicity or reciprocity")
    return tuple(output)


def build_twisted_reciprocal(
    c: int,
    r: int,
    character: str,
    *,
    d: int = 5,
) -> TwistedReciprocalSpec:
    """Build an exact candidate and its degree-12 quotient polynomial."""

    if d != 5:
        raise ValueError("the proved character presets currently require d=5")
    try:
        parameters = TWIST_PARAMETERS[character]
    except KeyError as exc:
        raise ValueError(f"unknown twist character {character!r}") from exc
    inner = critical_inner_coefficients(c, r, parameters["critical_value"])
    base = _convolve(inner, inner)
    base[0] -= d
    if len(base) != 13 or base[-1] != 1:
        raise AssertionError("base polynomial is not monic of degree twelve")
    polynomial = reciprocal_lift(base)
    return TwistedReciprocalSpec(
        character=character,
        c=int(c),
        r=int(r),
        d=d,
        target_t=int(parameters["target_t"]),
        inner_coefficients=inner,
        base_coefficients=tuple(base),
        coefficients=polynomial,
    )


def build_critical_twisted_reciprocal(
    critical_roots: Sequence[int],
    marked_root: int,
    critical_value: int,
    d: int,
    character: str,
) -> CriticalTwistedReciprocalSpec:
    """Build the five-simple-critical-point version of the construction.

    The three supplied roots augment ``-2`` and ``2`` as the five roots of
    ``g'``.  The caller chooses one marked critical value.  The exact product
    of the other critical values is then checked against the requested
    character, so an invalid parameterization cannot silently claim a label.
    """

    try:
        target_t = int(TWIST_PARAMETERS[character]["target_t"])
    except KeyError as exc:
        raise ValueError(f"unknown twist character {character!r}") from exc
    roots = tuple(int(value) for value in critical_roots)
    marked_root, critical_value, d = int(marked_root), int(critical_value), int(d)
    if len(roots) != 3 or len(set(roots)) != 3:
        raise ValueError("critical_roots must contain three distinct integers")
    if any(value in {-2, 2} for value in roots):
        raise ValueError("the three additional critical roots cannot be -2 or 2")
    if marked_root not in roots:
        raise ValueError("marked_root must be one of critical_roots")
    if d <= 0 or _is_rational_square(d):
        raise ValueError("d must be a positive nonsquare")

    derivative_factor: list[int] = [-4, 0, 1]
    for value in roots:
        derivative_factor = _convolve(derivative_factor, (-value, 1))
    inner = [0]
    for power, coefficient in enumerate(derivative_factor):
        numerator, denominator = 6 * coefficient, power + 1
        if numerator % denominator:
            raise ValueError("critical roots do not integrate to a monic integer sextic")
        inner.append(numerator // denominator)
    inner[0] = critical_value - _evaluate(inner, marked_root)

    base = _convolve(inner, inner)
    base[0] -= d
    critical_product = 1
    for value in roots:
        critical_product *= _evaluate(base, value)
    if character == "within":
        identity = _is_rational_square(critical_product)
    else:
        identity = _is_rational_square(d * critical_product)
    if not identity:
        raise ValueError("critical values do not realize the requested character")

    return CriticalTwistedReciprocalSpec(
        character=character,
        critical_roots=roots,
        marked_root=marked_root,
        critical_value=critical_value,
        d=d,
        target_t=target_t,
        inner_coefficients=tuple(inner),
        base_coefficients=tuple(base),
        coefficients=reciprocal_lift(base),
    )


def certify_twisted_reciprocal(
    spec: TwistedReciprocalSpec | CriticalTwistedReciprocalSpec,
    *,
    expected_root_count: int | None = None,
    label_probability: float = 0.9,
    gp: str = DEFAULT_GP,
    timeout: float = 1200,
    coefficient_limit: int = 10**55,
) -> TwistedReciprocalCandidate:
    """Check irreducibility, signature, and the exact square-class identity."""

    base = polynomial_expression(spec.base_coefficients, "x")
    polynomial = polynomial_expression(spec.coefficients, "x")
    started = time.perf_counter()
    script = f"""
f={base}; p={polynomial};
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(!polisirreducible(p),print("ERROR|lift-reducible");quit(1));
dc=core(poldisc(f)); ep=core(subst(f,x,2)*subst(f,x,-2));
print("ROOTS|",polsturm(p)); print("DISC_CORE|",dc); print("END_CORE|",ep);
print("NFDISC|",abs(nfdisc(p))); quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    values = {
        tag: value
        for line in output.splitlines()
        if "|" in line
        for tag, value in [line.strip().split("|", 1)]
        if tag in {"ERROR", "ROOTS", "DISC_CORE", "END_CORE", "NFDISC"}
    }
    cpu_ms = (time.perf_counter() - started) * 1000.0
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    roots = int(values["ROOTS"])
    if expected_root_count is not None and roots != int(expected_root_count):
        raise ValueError(f"signature mismatch: expected {expected_root_count}, got {roots}")
    discriminant_core = int(values["DISC_CORE"])
    endpoint_core = int(values["END_CORE"])
    expected_core = discriminant_core
    if spec.character == "product":
        # core(d*a) is not generally d*core(a); compare with PARI directly
        # through the equivalent quotient-square test below.
        identity = _is_rational_square(
            _evaluate(spec.base_coefficients, 2)
            * _evaluate(spec.base_coefficients, -2)
            * spec.d
            * discriminant_core
        )
    else:
        identity = endpoint_core == expected_core
    if not identity:
        raise AssertionError("engineered character square-class identity failed")
    if max(abs(value) for value in spec.coefficients) >= coefficient_limit:
        raise ValueError("coefficient limit exceeded")
    line = spec.line
    lineage = f"twisted-reciprocal:{spec.character}:12T299"
    parameter_key = ":".join(
        f"{key}={value}" for key, value in sorted(spec.parameters.items())
    )
    return TwistedReciprocalCandidate(
        coefficients=line,
        candidate_hash=candidate_hash(line),
        local_root_count=roots,
        local_irreducible=True,
        target_t=spec.target_t,
        target_r=roots,
        label_probability=float(label_probability),
        recipe_family="twisted_reciprocal_12T299",
        recipe_lineage=lineage,
        recipe_id=f"{lineage}:{parameter_key}",
        construction_overgroup="C2^11_character_twist_over_12T299",
        parameters={**spec.parameters, "character": spec.character},
        inner_coefficients=spec.inner_coefficients,
        base_coefficients=spec.base_coefficients,
        base_discriminant_squareclass=discriminant_core,
        endpoint_product_squareclass=endpoint_core,
        character_identity_verified=True,
        field_disc_abs=int(values["NFDISC"]),
        cpu_ms=cpu_ms,
    )


def _is_rational_square(value: int) -> bool:
    from math import isqrt

    value = int(value)
    return value >= 0 and isqrt(value) ** 2 == value
