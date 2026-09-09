#!/usr/bin/env python3
"""Deterministic degree-8 times degree-3 compositum construction."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from routeA.ledger import candidate_hash, canonical_coefficients


DEFAULT_GP = os.path.expanduser(os.environ.get("IGP24_GP", "").strip()) or (
    shutil.which("gp") or str(Path.home() / ".local/bin/gp")
)


@dataclass(frozen=True)
class ComponentPolynomial:
    degree: int
    coefficients: tuple[int, ...]
    transitive_id: int
    group_order: int
    group_name: str
    root_count: int
    discriminant_support: tuple[int, ...]
    discriminant: int
    component_id: str
    discriminant_squareclass: int | None = None

    @property
    def polynomial(self) -> str:
        return polynomial_expression(self.coefficients, "x")

    @property
    def group_label(self) -> str:
        return f"{self.degree}T{self.transitive_id}"

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["coefficients"] = list(self.coefficients)
        value["discriminant_support"] = list(self.discriminant_support)
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "ComponentPolynomial":
        return cls(
            degree=int(value["degree"]),
            coefficients=tuple(int(part) for part in value["coefficients"]),
            transitive_id=int(value["transitive_id"]),
            group_order=int(value["group_order"]),
            group_name=str(value["group_name"]),
            root_count=int(value["root_count"]),
            discriminant_support=tuple(int(part) for part in value["discriminant_support"]),
            discriminant=int(value["discriminant"]),
            component_id=str(value["component_id"]),
            discriminant_squareclass=int(value.get("discriminant_squareclass", value["discriminant"])),
        )


@dataclass(frozen=True)
class CompositumCandidate:
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
    octic_component_id: str
    cubic_component_id: str
    primitive_parameter: int
    cpu_ms: float
    discriminant_disjoint: bool
    estimated_nfdisc_abs: int | None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def classify_component(
    coefficients: Sequence[int],
    *,
    gp: str = DEFAULT_GP,
    timeout: float = 900,
) -> ComponentPolynomial:
    coeffs = tuple(int(value) for value in coefficients)
    degree = len(coeffs) - 1
    if degree not in {3, 8} or coeffs[-1] != 1:
        raise ValueError("component must be a monic cubic or octic")
    polynomial = polynomial_expression(coeffs, "x")
    script = f"""
default(new_galois_format, 1);
p={polynomial};
if(!polisirreducible(p), print("ERROR|reducible"); quit(1));
g=polgalois(p);
d=poldisc(p);
support=if(abs(d)==1, [], Vec(factor(abs(d))[,1]));
print("GROUP|",g[1],"|",g[2],"|",g[3],"|",g[4]);
print("ROOTS|",polsturm(p));
print("DISC|",d);
print("SQUARECLASS|",core(d));
print("SUPPORT|",support);
quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    values = _tagged_output(output)
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    group = values["GROUP"].split("|", 3)
    if len(group) != 4:
        raise RuntimeError(f"unexpected polgalois output: {values['GROUP']}")
    discriminant = int(values["DISC"])
    support = tuple(_parse_int_vector(values["SUPPORT"]))
    component_line = ",".join(str(value) for value in coeffs)
    component_digest = hashlib.sha256(
        f"component-degree={degree}|{component_line}".encode("ascii")
    ).hexdigest()
    component_id = f"{degree}c_{component_digest[:20]}"
    return ComponentPolynomial(
        degree=degree,
        coefficients=coeffs,
        transitive_id=int(group[2]),
        group_order=int(group[0]),
        group_name=group[3].strip('"'),
        root_count=int(values["ROOTS"]),
        discriminant_support=support,
        discriminant=discriminant,
        component_id=component_id,
        discriminant_squareclass=int(values["SQUARECLASS"]),
    )


def disjoint_ramification(octic: ComponentPolynomial, cubic: ComponentPolynomial) -> bool:
    if octic.degree != 8 or cubic.degree != 3:
        raise ValueError("expected an octic and a cubic component")
    return set(octic.discriminant_support).isdisjoint(cubic.discriminant_support)


def generate_compositum(
    octic: ComponentPolynomial,
    cubic: ComponentPolynomial,
    *,
    primitive_parameter: int,
    target_t: int,
    label_probability: float = 1.0,
    gp: str = DEFAULT_GP,
    timeout: float = 900,
    coefficient_limit: int = 10**55,
) -> CompositumCandidate:
    if primitive_parameter == 0:
        raise ValueError("primitive parameter must be nonzero")
    if octic.degree != 8 or cubic.degree != 3:
        raise ValueError("expected an octic and a cubic component")
    f8 = polynomial_expression(octic.coefficients, "x")
    h3 = polynomial_expression(cubic.coefficients, "y")
    started = time.perf_counter()
    script = f"""
f8={f8}; h3={h3}; c={int(primitive_parameter)};
p=subst(polresultant(h3, subst(f8,x,z-c*y), y), z, x);
if(poldegree(p)!=24, print("ERROR|degree"); quit(1));
if(polcoef(p,24)!=1, p=p/polcoef(p,24));
if(!polisirreducible(p), print("ERROR|reducible"); quit(1));
print("ROOTS|",polsturm(p));
print("VEC|",Vec(p));
quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    cpu_ms = (time.perf_counter() - started) * 1000.0
    values = _tagged_output(output)
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    descending = _parse_int_vector(values["VEC"])
    coeffs = list(reversed(descending))
    line = canonical_coefficients(coeffs)
    if max(abs(value) for value in coeffs) >= coefficient_limit:
        raise ValueError("coefficient limit exceeded")
    roots = int(values["ROOTS"])
    expected_roots = octic.root_count * cubic.root_count
    if roots != expected_roots:
        raise ValueError(f"signature mismatch: expected {expected_roots}, got {roots}")
    lineage = f"8x3:{octic.component_id}:{cubic.component_id}"
    recipe_id = f"{lineage}:c={primitive_parameter}"
    disjoint = disjoint_ramification(octic, cubic)
    estimated_nfdisc_abs = None
    if disjoint:
        # Coprime component discriminants imply an integral tensor product,
        # so the degree-(8*3) field discriminant is exact before submission.
        estimated_nfdisc_abs = abs(octic.discriminant) ** 3 * abs(cubic.discriminant) ** 8
    return CompositumCandidate(
        coefficients=line,
        candidate_hash=candidate_hash(line),
        local_root_count=roots,
        local_irreducible=True,
        target_t=int(target_t),
        target_r=roots,
        label_probability=float(label_probability),
        recipe_family="compositum_8x3",
        recipe_lineage=lineage,
        recipe_id=recipe_id,
        construction_overgroup=f"{octic.group_label}x{cubic.group_label}",
        octic_component_id=octic.component_id,
        cubic_component_id=cubic.component_id,
        primitive_parameter=int(primitive_parameter),
        cpu_ms=cpu_ms,
        discriminant_disjoint=disjoint,
        estimated_nfdisc_abs=estimated_nfdisc_abs,
    )


def polynomial_expression(coefficients: Sequence[int], variable: str) -> str:
    terms = []
    for exponent, coefficient in enumerate(coefficients):
        coefficient = int(coefficient)
        if coefficient:
            terms.append(f"({coefficient})*{variable}^{exponent}")
    return "+".join(terms) if terms else "0"


def _run_gp(script: str, *, gp: str, timeout: float) -> str:
    result = subprocess.run(
        [gp, "-q", "-f", "-s", "800000000"],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"PARI/GP failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def _tagged_output(output: str) -> dict[str, str]:
    values = {}
    for line in output.splitlines():
        if "|" not in line:
            continue
        tag, value = line.strip().split("|", 1)
        if tag in {
            "ERROR", "GROUP", "ROOTS", "DISC", "SQUARECLASS", "SUPPORT",
            "VEC", "VEC6", "VEC12",
        }:
            values[tag] = value
    return values


def _parse_int_vector(value: str) -> list[int]:
    value = value.strip()
    if value == "[]":
        return []
    parsed = ast.literal_eval(value)
    if not isinstance(parsed, (list, tuple)):
        raise ValueError(f"not a vector: {value}")
    return [int(item) for item in parsed]
