"""Unit-steered quadratic character lifts over degree-12 fields.

For a monic polynomial ``f`` of degree twelve and an element ``h`` of
``Q[y]/(f)``, the relative norm

    Res_y(f(y), x^2 - h(y))

has twice as many real roots as ``h`` has positive real embeddings.  Complex
embeddings contribute no real roots.  A fundamental unit changes the real
signs without changing the rational norm squareclass (provided its norm is
positive).  This module enumerates the unit squareclasses, keeps compact
representatives for requested signatures, reduces the resulting degree-24
polynomials, and applies the exact maximal-subgroup certificate used by the
nested-character campaign.

The caller is responsible for supplying the calibrated character-to-24T map.
The norm-squareclass check proves that every generated lift obeys that exact
index-two relation; the maximal-subgroup certificate then proves equality
with the claimed target rather than mere containment.
"""

from __future__ import annotations

import ast
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from routeA.constructions.compositum_8x3 import (
    DEFAULT_GP,
    _run_gp,
    polynomial_expression,
)
from routeA.constructions.nested_character_lift import (
    DEFAULT_GAP,
    prove_full_target_group,
)
from routeA.ledger import candidate_hash, canonical_coefficients


@dataclass(frozen=True)
class UnitCharacterFamily:
    """A calibrated degree-12 quotient and one of its Kummer characters."""

    family: str
    base_t: int
    target_t: int
    expected_norm_squareclass: int
    base_coefficients: tuple[int, ...]
    generator_coefficients: tuple[int, ...]
    target_root_counts: tuple[int, ...] = tuple(range(0, 25, 4))
    construction_overgroup: str = ""
    character_name: str = ""
    base_root_count: int = 12

    def __post_init__(self) -> None:
        if len(self.base_coefficients) != 13 or self.base_coefficients[-1] != 1:
            raise ValueError("base polynomial must be monic of degree twelve")
        if len(self.generator_coefficients) > 12:
            raise ValueError("Kummer generator must have degree below twelve")
        if not self.generator_coefficients or not any(self.generator_coefficients):
            raise ValueError("Kummer generator must be nonzero")
        roots = tuple(int(value) for value in self.target_root_counts)
        if any(value < 0 or value > 24 or value % 2 for value in roots):
            raise ValueError("target root counts must be even and in [0, 24]")
        base_roots = int(self.base_root_count)
        if base_roots < 0 or base_roots > 12 or base_roots % 2:
            raise ValueError("base root count must be even and in [0, 12]")
        if any(value > 2 * base_roots for value in roots):
            raise ValueError("target root count exceeds twice the real base embeddings")


@dataclass(frozen=True)
class UnitCharacterLiftSpec:
    family: UnitCharacterFamily
    target_r: int
    unit_mask: int
    unit_sign: int
    h_coefficients: tuple[int, ...]
    coefficients: tuple[int, ...]

    @property
    def pair(self) -> tuple[int, int]:
        return self.family.target_t, self.target_r

    @property
    def line(self) -> str:
        return canonical_coefficients(self.coefficients)


@dataclass(frozen=True)
class UnitCharacterLiftCandidate:
    coefficients: str
    candidate_hash: str
    local_root_count: int
    local_irreducible: bool
    target_t: int
    target_r: int
    label_probability: float
    valid_probability: float
    recipe_family: str
    recipe_lineage: str
    recipe_id: str
    construction_overgroup: str
    parameters: dict[str, Any]
    base_coefficients: tuple[int, ...]
    h_coefficients: tuple[int, ...]
    norm_squareclass: int
    expected_norm_squareclass: int
    exact_compatibility_proven: bool
    maximal_subgroups_excluded: tuple[str, ...]
    modular_witnesses: tuple[dict[str, Any], ...]
    field_disc_abs: int
    estimated_nfdisc_abs: int
    submission_ready: bool
    cpu_ms: float

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["base_coefficients"] = list(self.base_coefficients)
        value["h_coefficients"] = list(self.h_coefficients)
        value["maximal_subgroups_excluded"] = list(self.maximal_subgroups_excluded)
        value["modular_witnesses"] = list(self.modular_witnesses)
        return value


@dataclass(frozen=True)
class _GeneratorOption:
    target_r: int
    height: int
    unit_mask: int
    unit_sign: int
    h_coefficients: tuple[int, ...]


def enumerate_unit_generators(
    family: UnitCharacterFamily,
    *,
    gp: str = DEFAULT_GP,
    timeout: float = 1200,
    options_per_signature: int = 12,
) -> dict[int, list[_GeneratorOption]]:
    """Return compact unit-squareclass representatives for each signature."""

    if options_per_signature <= 0:
        raise ValueError("options_per_signature must be positive")
    base = polynomial_expression(family.base_coefficients, "x")
    h0 = polynomial_expression(family.generator_coefficients, "x")
    wanted = ",".join(str(int(value)) for value in family.target_root_counts)
    script = f"""
f={base}; h0={h0}; wanted=[{wanted}];
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(polsturm(f)!={int(family.base_root_count)},print("ERROR|base-root-count");quit(1));
b=bnfinit(f); fu=b.fu;
for(mask=0,2^#fu-1,h=Mod(h0,f);for(i=1,#fu,if(bittest(mask,i-1),h*=fu[i]));for(si=1,2,s=if(si==1,1,-1);hh=lift(s*h);den=denominator(content(hh));hi=lift(Mod(hh*den^2,f));c=content(hi);if(c!=0,hi=hi/(c/core(c)));emb=nfeltembed(b,hi);r=2*sum(i=1,{int(family.base_root_count)},real(emb[i])>0);if(setsearch(wanted,r),v=vector(12,i,polcoef(hi,i-1));height=vecmax(vector(12,i,abs(v[i])));print("CAND|",r,"|",height,"|",mask,"|",s,"|",v))));quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    if "ERROR|" in output:
        error = next(line for line in output.splitlines() if line.startswith("ERROR|"))
        raise ValueError(error.split("|", 1)[1])
    by_signature: dict[int, list[_GeneratorOption]] = {
        int(value): [] for value in family.target_root_counts
    }
    seen: dict[int, set[tuple[int, ...]]] = {key: set() for key in by_signature}
    for line in output.splitlines():
        if not line.startswith("CAND|"):
            continue
        _, raw_r, raw_height, raw_mask, raw_sign, raw_vector = line.split("|", 5)
        target_r = int(raw_r)
        coefficients = tuple(int(value) for value in ast.literal_eval(raw_vector))
        if coefficients in seen[target_r]:
            continue
        seen[target_r].add(coefficients)
        by_signature[target_r].append(_GeneratorOption(
            target_r=target_r,
            height=int(raw_height),
            unit_mask=int(raw_mask),
            unit_sign=int(raw_sign),
            h_coefficients=_trim_coefficients(coefficients),
        ))
    missing = [value for value, options in by_signature.items() if not options]
    if missing:
        raise ValueError(f"unit signatures unavailable for root counts {missing}")
    for value, options in by_signature.items():
        by_signature[value] = sorted(
            options,
            key=lambda option: (option.height, option.unit_mask, -option.unit_sign),
        )[:options_per_signature]
    return by_signature


def build_reduced_unit_lift(
    family: UnitCharacterFamily,
    option: _GeneratorOption,
    *,
    gp: str = DEFAULT_GP,
    timeout: float = 1200,
    coefficient_limit: int = 10**55,
) -> UnitCharacterLiftSpec:
    """Build and ``polredabs`` one unit-steered relative quadratic lift."""

    base = polynomial_expression(family.base_coefficients, "y")
    h = polynomial_expression(option.h_coefficients, "y")
    script = f"""
y='y; x='x; f={base}; h={h};
p=polresultant(f,x^2-h,y);
if(poldegree(p)!=24,print("ERROR|degree");quit(1));
if(!polisirreducible(p),print("ERROR|lift-reducible");quit(1));
pr=polredabs(p);
if(poldegree(pr)!=24,print("ERROR|reduction-degree");quit(1));
print("ROOTS|",polsturm(pr)); print("VEC|",Vecrev(Vec(pr))); quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    values = _tagged(output, {"ERROR", "ROOTS", "VEC"})
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    roots = int(values["ROOTS"])
    if roots != option.target_r:
        raise ValueError(f"signature mismatch: expected {option.target_r}, got {roots}")
    coefficients = tuple(int(value) for value in ast.literal_eval(values["VEC"]))
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("reduced lift is not monic of degree twenty-four")
    if max(abs(value) for value in coefficients) >= int(coefficient_limit):
        raise ValueError("coefficient limit exceeded")
    return UnitCharacterLiftSpec(
        family=family,
        target_r=option.target_r,
        unit_mask=option.unit_mask,
        unit_sign=option.unit_sign,
        h_coefficients=option.h_coefficients,
        coefficients=coefficients,
    )


def build_unit_character_bank(
    family: UnitCharacterFamily,
    *,
    gp: str = DEFAULT_GP,
    gap: str = DEFAULT_GAP,
    timeout: float = 1200,
    prime_limit: int = 10000,
    options_per_signature: int = 12,
) -> list[UnitCharacterLiftCandidate]:
    """Find and certify one full-group candidate for every requested row."""

    options = enumerate_unit_generators(
        family,
        gp=gp,
        timeout=timeout,
        options_per_signature=options_per_signature,
    )
    certified: list[UnitCharacterLiftCandidate] = []
    failures: dict[int, list[str]] = {}
    for target_r in family.target_root_counts:
        for option in options[int(target_r)]:
            try:
                spec = build_reduced_unit_lift(family, option, gp=gp, timeout=timeout)
                candidate = certify_unit_character_lift(
                    spec,
                    gp=gp,
                    gap=gap,
                    timeout=timeout,
                    prime_limit=prime_limit,
                )
            except (RuntimeError, ValueError) as exc:
                failures.setdefault(int(target_r), []).append(str(exc))
                continue
            certified.append(candidate)
            break
        else:
            detail = "; ".join(failures.get(int(target_r), [])[-3:])
            raise ValueError(
                f"no full 24T{family.target_t} lift found for r={target_r}: {detail}"
            )
    return certified


def certify_unit_character_lift(
    spec: UnitCharacterLiftSpec,
    *,
    gp: str = DEFAULT_GP,
    gap: str = DEFAULT_GAP,
    timeout: float = 1200,
    prime_limit: int = 10000,
) -> UnitCharacterLiftCandidate:
    """Certify arithmetic, signature, norm character, and exact target group."""

    family = spec.family
    base = polynomial_expression(family.base_coefficients, "x")
    h = polynomial_expression(spec.h_coefficients, "x")
    polynomial = polynomial_expression(spec.coefficients, "x")
    started = time.perf_counter()
    script = f"""
f={base}; h={h}; p={polynomial};
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(polsturm(f)!={int(family.base_root_count)},print("ERROR|base-root-count");quit(1));
if(!polisirreducible(p),print("ERROR|lift-reducible");quit(1));
print("ROOTS|",polsturm(p));
print("NORM_CORE|",core(polresultant(f,h)));
print("NFDISC|",abs(nfdisc(p))); quit;
"""
    values = _tagged(
        _run_gp(script, gp=gp, timeout=timeout),
        {"ERROR", "ROOTS", "NORM_CORE", "NFDISC"},
    )
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    roots = int(values["ROOTS"])
    if roots != spec.target_r:
        raise ValueError(f"signature mismatch: expected {spec.target_r}, got {roots}")
    norm_core = int(values["NORM_CORE"])
    if norm_core != family.expected_norm_squareclass:
        raise ValueError(
            "Kummer character mismatch: expected squareclass "
            f"{family.expected_norm_squareclass}, got {norm_core}"
        )
    excluded, witnesses = prove_full_target_group(
        spec.coefficients,
        family.target_t,
        gap=gap,
        prime_limit=prime_limit,
        timeout=timeout,
    )
    field_disc = int(values["NFDISC"])
    line = spec.line
    lineage = f"unit-character-lift:12T{family.base_t}:24T{family.target_t}"
    key = candidate_hash(line)
    return UnitCharacterLiftCandidate(
        coefficients=line,
        candidate_hash=key,
        local_root_count=roots,
        local_irreducible=True,
        target_t=family.target_t,
        target_r=roots,
        label_probability=1.0,
        valid_probability=1.0,
        recipe_family=family.family,
        recipe_lineage=lineage,
        recipe_id=f"{lineage}:{key[:20]}",
        construction_overgroup=(
            family.construction_overgroup
            or f"index_two_character_lift_over_12T{family.base_t}"
        ),
        parameters={
            "base_t": family.base_t,
            "target_t": family.target_t,
            "character": family.character_name,
            "norm_squareclass": family.expected_norm_squareclass,
            "base_root_count": family.base_root_count,
            "unit_mask": spec.unit_mask,
            "unit_sign": spec.unit_sign,
        },
        base_coefficients=family.base_coefficients,
        h_coefficients=spec.h_coefficients,
        norm_squareclass=norm_core,
        expected_norm_squareclass=family.expected_norm_squareclass,
        exact_compatibility_proven=True,
        maximal_subgroups_excluded=tuple(excluded),
        modular_witnesses=tuple(witnesses),
        field_disc_abs=field_disc,
        estimated_nfdisc_abs=field_disc,
        submission_ready=True,
        cpu_ms=(time.perf_counter() - started) * 1000.0,
    )


def family_from_polynomials(
    *,
    family: str,
    base_t: int,
    target_t: int,
    expected_norm_squareclass: int,
    base_coefficients: Sequence[int],
    generator_coefficients: Sequence[int],
    target_root_counts: Iterable[int] = range(0, 25, 4),
    construction_overgroup: str = "",
    character_name: str = "",
    base_root_count: int = 12,
) -> UnitCharacterFamily:
    """Normalize a family definition supplied by a campaign builder."""

    return UnitCharacterFamily(
        family=str(family),
        base_t=int(base_t),
        target_t=int(target_t),
        expected_norm_squareclass=int(expected_norm_squareclass),
        base_coefficients=tuple(int(value) for value in base_coefficients),
        generator_coefficients=_trim_coefficients(generator_coefficients),
        target_root_counts=tuple(int(value) for value in target_root_counts),
        construction_overgroup=str(construction_overgroup),
        character_name=str(character_name),
        base_root_count=int(base_root_count),
    )


def _trim_coefficients(coefficients: Sequence[int]) -> tuple[int, ...]:
    values = [int(value) for value in coefficients]
    while len(values) > 1 and values[-1] == 0:
        values.pop()
    return tuple(values)


def _tagged(output: str, names: set[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "|" not in line:
            continue
        tag, value = line.strip().split("|", 1)
        if tag in names:
            values[tag] = value
    return values


__all__ = [
    "UnitCharacterFamily",
    "UnitCharacterLiftCandidate",
    "UnitCharacterLiftSpec",
    "build_reduced_unit_lift",
    "build_unit_character_bank",
    "certify_unit_character_lift",
    "enumerate_unit_generators",
    "family_from_polynomials",
]
