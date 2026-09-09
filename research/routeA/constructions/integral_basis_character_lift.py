"""Quadratic character lifts from integral-basis norm solutions.

PARI represents an algebraic integer in the power basis with rational
coefficients whenever the defining polynomial does not give an integral
basis.  Clearing those denominators before forming ``x^2-h`` can introduce
huge, artificial ramification.  This module keeps the rational power-basis
coordinates intact and asks ``rnfequation`` for an integral absolute equation
of the same relative quadratic field.

The resulting degree-24 polynomial is certified with the same exact
maximal-subgroup proof as the ordinary unit-character construction.
"""

from __future__ import annotations

import ast
import time
from dataclasses import asdict, dataclass
from fractions import Fraction
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
class IntegralBasisCharacterFamily:
    family: str
    base_t: int
    target_t: int
    expected_norm_squareclass: int
    base_coefficients: tuple[int, ...]
    seed_coefficients: tuple[Fraction, ...]
    target_root_counts: tuple[int, ...] = tuple(range(0, 25, 4))
    construction_overgroup: str = ""
    character_name: str = ""
    base_root_count: int = 12

    def __post_init__(self) -> None:
        if len(self.base_coefficients) != 13 or self.base_coefficients[-1] != 1:
            raise ValueError("base polynomial must be monic of degree twelve")
        if not self.seed_coefficients or len(self.seed_coefficients) > 12:
            raise ValueError("seed must be a nonzero element of the degree-12 field")
        if not any(self.seed_coefficients):
            raise ValueError("seed must be nonzero")
        if self.base_root_count < 0 or self.base_root_count > 12:
            raise ValueError("base_root_count must lie in [0, 12]")
        if self.base_root_count % 2:
            raise ValueError("base_root_count must be even")
        if any(
            root < 0 or root > 2 * self.base_root_count or root % 2
            for root in self.target_root_counts
        ):
            raise ValueError("invalid requested root count")


@dataclass(frozen=True)
class IntegralBasisGeneratorOption:
    target_r: int
    height: int
    unit_mask: int
    unit_sign: int
    h_coefficients: tuple[Fraction, ...]


@dataclass(frozen=True)
class IntegralBasisCharacterLiftSpec:
    family: IntegralBasisCharacterFamily
    target_r: int
    unit_mask: int
    unit_sign: int
    h_coefficients: tuple[Fraction, ...]
    coefficients: tuple[int, ...]

    @property
    def line(self) -> str:
        return canonical_coefficients(self.coefficients)


@dataclass(frozen=True)
class IntegralBasisCharacterLiftCandidate:
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
    h_coefficients: tuple[str, ...]
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


def enumerate_integral_basis_generators(
    family: IntegralBasisCharacterFamily,
    *,
    gp: str = DEFAULT_GP,
    timeout: float = 1200,
    options_per_signature: int = 16,
    allow_missing_signatures: bool = False,
) -> dict[int, list[IntegralBasisGeneratorOption]]:
    """Enumerate compact unit-squareclass representatives without clearing denominators."""

    if options_per_signature <= 0:
        raise ValueError("options_per_signature must be positive")
    base = polynomial_expression(family.base_coefficients, "x")
    seed = _fractional_polynomial_expression(family.seed_coefficients, "x")
    wanted = ",".join(str(value) for value in family.target_root_counts)
    script = f"""
x='x; f={base}; h0={seed}; wanted=[{wanted}];
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(polsturm(f)!={family.base_root_count},print("ERROR|base-root-count");quit(1));
b=bnfinit(f); fu=b.fu;
for(mask=0,2^#fu-1,h=Mod(h0,f);for(i=1,#fu,if(bittest(mask,i-1),h*=fu[i]));for(si=1,2,s=if(si==1,1,-1);hh=lift(s*h);emb=nfeltembed(b,hh);r=2*sum(i=1,{family.base_root_count},real(emb[i])>0);if(setsearch(wanted,r),vn=vector(12,i,numerator(polcoef(hh,i-1)));vd=vector(12,i,denominator(polcoef(hh,i-1)));height=vecmax(vector(12,i,max(abs(vn[i]),vd[i])));print("CAND|",r,"|",height,"|",mask,"|",s,"|",vn,"|",vd))));quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    if "ERROR|" in output:
        error = next(line for line in output.splitlines() if line.startswith("ERROR|"))
        raise ValueError(error.split("|", 1)[1])
    rows: dict[int, list[IntegralBasisGeneratorOption]] = {
        root: [] for root in family.target_root_counts
    }
    seen: dict[int, set[tuple[Fraction, ...]]] = {root: set() for root in rows}
    for line in output.splitlines():
        if not line.startswith("CAND|"):
            continue
        _, raw_r, raw_height, raw_mask, raw_sign, raw_num, raw_den = line.split("|", 6)
        target_r = int(raw_r)
        numerators = ast.literal_eval(raw_num)
        denominators = ast.literal_eval(raw_den)
        coefficients = _trim_fractions(
            Fraction(int(num), int(den))
            for num, den in zip(numerators, denominators)
        )
        if coefficients in seen[target_r]:
            continue
        seen[target_r].add(coefficients)
        rows[target_r].append(IntegralBasisGeneratorOption(
            target_r=target_r,
            height=int(raw_height),
            unit_mask=int(raw_mask),
            unit_sign=int(raw_sign),
            h_coefficients=coefficients,
        ))
    missing = [root for root, options in rows.items() if not options]
    if missing and not allow_missing_signatures:
        raise ValueError(f"unit signatures unavailable for root counts {missing}")
    for root, options in rows.items():
        rows[root] = sorted(
            options,
            key=lambda option: (option.height, option.unit_mask, -option.unit_sign),
        )[:options_per_signature]
    return rows


def build_integral_basis_lift(
    family: IntegralBasisCharacterFamily,
    option: IntegralBasisGeneratorOption,
    *,
    gp: str = DEFAULT_GP,
    timeout: float = 1200,
    coefficient_limit: int = 10**55,
    absolute_reduction: bool = True,
) -> IntegralBasisCharacterLiftSpec:
    """Use ``rnfequation`` to retain the integral-basis arithmetic."""

    base = polynomial_expression(family.base_coefficients, "t")
    h = _fractional_polynomial_expression(option.h_coefficients, "t")
    reduction = "polredabs(p)" if absolute_reduction else "polredbest(p)"
    script = f"""
z='z; t='t; f={base}; h={h}; b=bnfinit(f);
p=rnfequation(b,z^2-Mod(h,f));
if(poldegree(p)!=24,print("ERROR|degree");quit(1));
if(!polisirreducible(p),print("ERROR|lift-reducible");quit(1));
pr={reduction};
print("ROOTS|",polsturm(pr)); print("VEC|",Vecrev(Vec(pr))); quit;
"""
    values = _tagged(_run_gp(script, gp=gp, timeout=timeout), {"ERROR", "ROOTS", "VEC"})
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    roots = int(values["ROOTS"])
    if roots != option.target_r:
        raise ValueError(f"signature mismatch: expected {option.target_r}, got {roots}")
    coefficients = tuple(int(value) for value in ast.literal_eval(values["VEC"]))
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("absolute lift is not monic of degree twenty-four")
    if max(abs(value) for value in coefficients) >= coefficient_limit:
        raise ValueError("coefficient limit exceeded")
    return IntegralBasisCharacterLiftSpec(
        family=family,
        target_r=roots,
        unit_mask=option.unit_mask,
        unit_sign=option.unit_sign,
        h_coefficients=option.h_coefficients,
        coefficients=coefficients,
    )


def certify_integral_basis_lift(
    spec: IntegralBasisCharacterLiftSpec,
    *,
    gp: str = DEFAULT_GP,
    gap: str = DEFAULT_GAP,
    timeout: float = 1200,
    prime_limit: int = 10000,
) -> IntegralBasisCharacterLiftCandidate:
    family = spec.family
    base = polynomial_expression(family.base_coefficients, "t")
    h = _fractional_polynomial_expression(spec.h_coefficients, "t")
    polynomial = polynomial_expression(spec.coefficients, "z")
    started = time.perf_counter()
    script = f"""
z='z; t='t; f={base}; h={h}; p={polynomial}; b=bnfinit(f);
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(polsturm(f)!={family.base_root_count},print("ERROR|base-root-count");quit(1));
if(!polisirreducible(p),print("ERROR|lift-reducible");quit(1));
n=nfeltnorm(b,Mod(h,f));
print("ROOTS|",polsturm(p));
print("NORM_CORE|",core(numerator(n)*denominator(n)));
print("NFDISC|",abs(nfdisc(p))); quit;
"""
    values = _tagged(
        _run_gp(script, gp=gp, timeout=timeout),
        {"ERROR", "ROOTS", "NORM_CORE", "NFDISC"},
    )
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    roots = int(values["ROOTS"])
    norm_core = int(values["NORM_CORE"])
    if roots != spec.target_r:
        raise ValueError(f"signature mismatch: expected {spec.target_r}, got {roots}")
    if norm_core != family.expected_norm_squareclass:
        raise ValueError(
            f"Kummer character mismatch: expected {family.expected_norm_squareclass}, "
            f"got {norm_core}"
        )
    excluded, witnesses = prove_full_target_group(
        spec.coefficients,
        family.target_t,
        gap=gap,
        prime_limit=prime_limit,
        timeout=timeout,
    )
    line = spec.line
    key = candidate_hash(line)
    lineage = f"integral-basis-character-lift:12T{family.base_t}:24T{family.target_t}"
    return IntegralBasisCharacterLiftCandidate(
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
            "integral_basis_preserved": True,
        },
        base_coefficients=family.base_coefficients,
        h_coefficients=tuple(_fraction_text(value) for value in spec.h_coefficients),
        norm_squareclass=norm_core,
        expected_norm_squareclass=family.expected_norm_squareclass,
        exact_compatibility_proven=True,
        maximal_subgroups_excluded=tuple(excluded),
        modular_witnesses=tuple(witnesses),
        field_disc_abs=int(values["NFDISC"]),
        estimated_nfdisc_abs=int(values["NFDISC"]),
        submission_ready=True,
        cpu_ms=(time.perf_counter() - started) * 1000.0,
    )


def build_integral_basis_character_bank(
    family: IntegralBasisCharacterFamily,
    *,
    gp: str = DEFAULT_GP,
    gap: str = DEFAULT_GAP,
    timeout: float = 1200,
    prime_limit: int = 10000,
    options_per_signature: int = 16,
    coefficient_limit: int = 10**55,
    skip_unavailable_signatures: bool = False,
) -> list[IntegralBasisCharacterLiftCandidate]:
    options = enumerate_integral_basis_generators(
        family,
        gp=gp,
        timeout=timeout,
        options_per_signature=options_per_signature,
        allow_missing_signatures=skip_unavailable_signatures,
    )
    certified: list[IntegralBasisCharacterLiftCandidate] = []
    failures: dict[int, list[str]] = {}
    for target_r in family.target_root_counts:
        if not options[target_r] and skip_unavailable_signatures:
            continue
        for option in options[target_r]:
            try:
                spec = build_integral_basis_lift(
                    family,
                    option,
                    gp=gp,
                    timeout=timeout,
                    coefficient_limit=coefficient_limit,
                )
                candidate = certify_integral_basis_lift(
                    spec,
                    gp=gp,
                    gap=gap,
                    timeout=timeout,
                    prime_limit=prime_limit,
                )
            except (RuntimeError, ValueError) as exc:
                failures.setdefault(target_r, []).append(str(exc))
                continue
            certified.append(candidate)
            break
        else:
            detail = "; ".join(failures.get(target_r, [])[-3:])
            raise ValueError(
                f"no full 24T{family.target_t} lift found for r={target_r}: {detail}"
            )
    return certified


def family_from_integral_basis_seed(
    *,
    family: str,
    base_t: int,
    target_t: int,
    expected_norm_squareclass: int,
    base_coefficients: Sequence[int],
    seed_coefficients: Sequence[int | str | Fraction],
    target_root_counts: Iterable[int] = range(0, 25, 4),
    construction_overgroup: str = "",
    character_name: str = "",
    base_root_count: int = 12,
) -> IntegralBasisCharacterFamily:
    return IntegralBasisCharacterFamily(
        family=str(family),
        base_t=int(base_t),
        target_t=int(target_t),
        expected_norm_squareclass=int(expected_norm_squareclass),
        base_coefficients=tuple(int(value) for value in base_coefficients),
        seed_coefficients=_trim_fractions(Fraction(value) for value in seed_coefficients),
        target_root_counts=tuple(int(value) for value in target_root_counts),
        construction_overgroup=str(construction_overgroup),
        character_name=str(character_name),
        base_root_count=int(base_root_count),
    )


def _fractional_polynomial_expression(coefficients: Sequence[Fraction], variable: str) -> str:
    terms = []
    for power, raw in enumerate(coefficients):
        value = Fraction(raw)
        if not value:
            continue
        coefficient = _fraction_text(value)
        terms.append(f"({coefficient})" if power == 0 else f"({coefficient})*{variable}^{power}")
    return "+".join(terms) or "0"


def _fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _trim_fractions(values: Iterable[Fraction]) -> tuple[Fraction, ...]:
    output = [Fraction(value) for value in values]
    while len(output) > 1 and not output[-1]:
        output.pop()
    return tuple(output)


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
    "IntegralBasisCharacterFamily",
    "IntegralBasisCharacterLiftCandidate",
    "IntegralBasisCharacterLiftSpec",
    "IntegralBasisGeneratorOption",
    "build_integral_basis_character_bank",
    "build_integral_basis_lift",
    "certify_integral_basis_lift",
    "enumerate_integral_basis_generators",
    "family_from_integral_basis_seed",
]
