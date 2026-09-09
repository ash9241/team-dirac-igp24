"""Genuine cubic -> quadratic -> quadratic -> quadratic tower generator."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from routeA.constructions.compositum_8x3 import (
    DEFAULT_GP,
    _parse_int_vector,
    _run_gp,
    _tagged_output,
    polynomial_expression,
)
from routeA.ledger import candidate_hash, canonical_coefficients


@dataclass(frozen=True)
class QuadraticTowerCandidate:
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
    parameters: Mapping[str, Any]
    intermediate_degree6_coefficients: tuple[int, ...]
    intermediate_degree12_coefficients: tuple[int, ...]
    cpu_ms: float

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["intermediate_degree6_coefficients"] = list(
            self.intermediate_degree6_coefficients
        )
        value["intermediate_degree12_coefficients"] = list(
            self.intermediate_degree12_coefficients
        )
        return value


def generate_quadratic_tower(
    cubic_coefficients: Sequence[int],
    parameters: Mapping[str, Sequence[int]],
    *,
    target_t: int,
    label_probability: float = 0.25,
    cluster_id: str = "unclassified",
    gp: str = DEFAULT_GP,
    timeout: float = 1200,
    coefficient_limit: int = 10**55,
    second_primitive_base_shift: int = 0,
    second_primitive_u_shift: int = 0,
    final_primitive_base_shift: int = 0,
    final_primitive_u_shift: int = 0,
    final_primitive_v_shift: int = 0,
) -> QuadraticTowerCandidate:
    cubic = tuple(int(value) for value in cubic_coefficients)
    if len(cubic) != 4 or cubic[-1] != 1:
        raise ValueError("base must be a monic cubic")
    required = ("a1", "a2_A", "a2_B", "a3_A", "a3_B", "a3_C", "a3_D")
    if any(name not in parameters for name in required):
        raise ValueError(f"parameters must contain {required}")
    normalized = {name: tuple(int(value) for value in parameters[name]) for name in required}
    if any(not value or len(value) > 3 for value in normalized.values()):
        raise ValueError("tower coefficient polynomials must have degree below three")

    f = polynomial_expression(cubic, "x")
    expr = {name: polynomial_expression(value, "x") for name, value in normalized.items()}
    started = time.perf_counter()
    script = f"""
f={f};
F1=u^2-({expr['a1']});
F2=v^2-(({expr['a2_A']})+({expr['a2_B']})*u);
F3=z^2-(({expr['a3_A']})+({expr['a3_B']})*u+({expr['a3_C']})*v+({expr['a3_D']})*u*v);
P6=subst(polresultant(f,F1,x),u,x);
if(polcoef(P6,6)!=1,P6=P6/polcoef(P6,6));
F2primitive=subst(F2,v,y-({int(second_primitive_base_shift)})*x-({int(second_primitive_u_shift)})*u);
R2u=polresultant(F2primitive,F1,u); P12=subst(polresultant(R2u,f,x),y,x);
if(polcoef(P12,12)!=1,P12=P12/polcoef(P12,12));
if(poldegree(P6)!=6 || !polisirreducible(P6), print("ERROR|degree6"); quit(1));
if(poldegree(P12)!=12 || !polisirreducible(P12), print("ERROR|degree12"); quit(1));
F3primitive=subst(F3,z,y-({int(final_primitive_base_shift)})*x-({int(final_primitive_u_shift)})*u-({int(final_primitive_v_shift)})*v);
Rv=polresultant(F3primitive,F2,v); Ru=polresultant(Rv,F1,u);
p=subst(polresultant(Ru,f,x),y,x);
if(poldegree(p)!=24, print("ERROR|degree"); quit(1));
if(polcoef(p,24)!=1,p=p/polcoef(p,24));
if(!polisirreducible(p), print("ERROR|reducible"); quit(1));
print("ROOTS|",polsturm(p)); print("VEC6|",Vec(P6));
print("VEC12|",Vec(P12)); print("VEC|",Vec(p)); quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    cpu_ms = (time.perf_counter() - started) * 1000.0
    values = _tagged_output(output)
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    coefficients = list(reversed(_parse_int_vector(values["VEC"])))
    intermediate6 = tuple(reversed(_parse_int_vector(values["VEC6"])))
    intermediate12 = tuple(reversed(_parse_int_vector(values["VEC12"])))
    line = canonical_coefficients(coefficients)
    if max(abs(value) for value in coefficients) >= coefficient_limit:
        raise ValueError("coefficient limit exceeded")
    roots = int(values["ROOTS"])
    serialized = {name: list(value) for name, value in normalized.items()}
    primitive_shifts = {
        "second_base": int(second_primitive_base_shift),
        "second_u": int(second_primitive_u_shift),
        "final_base": int(final_primitive_base_shift),
        "final_u": int(final_primitive_u_shift),
        "final_v": int(final_primitive_v_shift),
    }
    recipe_suffix = ";".join(f"{name}={','.join(map(str, value))}" for name, value in normalized.items())
    lineage = f"quadratic-tower-cubic:{cluster_id}"
    return QuadraticTowerCandidate(
        coefficients=line,
        candidate_hash=candidate_hash(line),
        local_root_count=roots,
        local_irreducible=True,
        target_t=int(target_t),
        target_r=roots,
        label_probability=float(label_probability),
        recipe_family="quadratic_tower_over_cubic",
        recipe_lineage=lineage,
        recipe_id=f"{lineage}:{recipe_suffix}",
        construction_overgroup="iterated_C2_wreath_over_G3",
        parameters={
            "cubic": list(cubic),
            **serialized,
            "primitive_shifts": primitive_shifts,
        },
        intermediate_degree6_coefficients=intermediate6,
        intermediate_degree12_coefficients=intermediate12,
        cpu_ms=cpu_ms,
    )
