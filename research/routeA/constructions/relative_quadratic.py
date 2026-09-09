"""Relative quadratic signature construction over a degree-12 seed."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from routeA.constructions.compositum_8x3 import (
    DEFAULT_GP,
    _parse_int_vector,
    _run_gp,
    _tagged_output,
    polynomial_expression,
)
from routeA.ledger import candidate_hash, canonical_coefficients


@dataclass(frozen=True)
class RelativeQuadraticCandidate:
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
    seed_coefficients: tuple[int, ...]
    radicand_coefficients: tuple[int, ...]
    cpu_ms: float

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["seed_coefficients"] = list(self.seed_coefficients)
        value["radicand_coefficients"] = list(self.radicand_coefficients)
        return value


def generate_relative_quadratic(
    seed_coefficients: Sequence[int],
    radicand_coefficients: Sequence[int],
    *,
    target_t: int,
    expected_root_count: int | None = None,
    label_probability: float = 0.5,
    seed_id: str = "degree12",
    gp: str = DEFAULT_GP,
    timeout: float = 900,
    coefficient_limit: int = 10**55,
) -> RelativeQuadraticCandidate:
    seed = tuple(int(value) for value in seed_coefficients)
    radicand = tuple(int(value) for value in radicand_coefficients)
    if len(seed) != 13 or seed[-1] != 1:
        raise ValueError("seed must be a monic degree-12 polynomial")
    if not radicand or len(radicand) > 12:
        raise ValueError("radicand must have degree below 12")
    f12 = polynomial_expression(seed, "x")
    a = polynomial_expression(radicand, "x")
    started = time.perf_counter()
    script = f"""
f={f12}; a={a};
if(!polisirreducible(f), print("ERROR|seed-reducible"); quit(1));
p=subst(polresultant(f,z^2-a,x),z,x);
if(poldegree(p)!=24, print("ERROR|degree"); quit(1));
if(polcoef(p,24)!=1,p=p/polcoef(p,24));
if(!polisirreducible(p), print("ERROR|reducible"); quit(1));
print("ROOTS|",polsturm(p)); print("VEC|",Vec(p)); quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    cpu_ms = (time.perf_counter() - started) * 1000.0
    values = _tagged_output(output)
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    roots = int(values["ROOTS"])
    if expected_root_count is not None and roots != expected_root_count:
        raise ValueError(f"signature mismatch: expected {expected_root_count}, got {roots}")
    coefficients = list(reversed(_parse_int_vector(values["VEC"])))
    line = canonical_coefficients(coefficients)
    if max(abs(value) for value in coefficients) >= coefficient_limit:
        raise ValueError("coefficient limit exceeded")
    radicand_key = ".".join(map(str, radicand))
    lineage = f"relative-quadratic:{seed_id}"
    return RelativeQuadraticCandidate(
        coefficients=line,
        candidate_hash=candidate_hash(line),
        local_root_count=roots,
        local_irreducible=True,
        target_t=int(target_t),
        target_r=roots,
        label_probability=float(label_probability),
        recipe_family="relative_quadratic_degree12",
        recipe_lineage=lineage,
        recipe_id=f"{lineage}:a={radicand_key}",
        construction_overgroup="C2_wr_G12",
        seed_coefficients=seed,
        radicand_coefficients=radicand,
        cpu_ms=cpu_ms,
    )
