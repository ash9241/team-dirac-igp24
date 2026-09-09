"""Exact quadratic character lifts above nested degree-12 polynomials.

The degree-12 quotient is

    f(y) = (q(y)^2 - e)^2 - d

and a degree-24 candidate is the relative quadratic norm

    P(x) = Res_y(f(y), x^2 - h(y)).

The square class of ``Res(f, h)`` selects the index-two Kummer character.
For the calibrated quotient groups used here, the character-to-T-number map
is exact.  Certification also proves that the candidate is not contained in
any transitive maximal subgroup of the claimed degree-24 group.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from sympy import Poly, resultant, symbols

from routeA.constructions.compositum_8x3 import (
    DEFAULT_GP,
    _run_gp,
    polynomial_expression,
)
from routeA.ledger import candidate_hash, canonical_coefficients


_X, _Y = symbols("x y")
_BUNDLED_GAP = Path("/tmp/igp24-gap/gap-4.16.0/gap")
DEFAULT_GAP = os.path.expanduser(os.environ.get("IGP24_GAP", "")) or (
    str(_BUNDLED_GAP) if _BUNDLED_GAP.exists() else (shutil.which("gap") or "gap")
)


@dataclass(frozen=True)
class NestedCharacterLiftSpec:
    family: str
    base_t: int
    target_t: int
    expected_norm_squareclass: int
    q_coefficients: tuple[int, ...]
    e: int
    d: int
    h_sign: int
    h_linear_roots: tuple[int, ...]
    h_positive_quadratics: tuple[tuple[int, int], ...]
    base_coefficients: tuple[int, ...]
    h_coefficients: tuple[int, ...]
    coefficients: tuple[int, ...]

    @property
    def line(self) -> str:
        return canonical_coefficients(self.coefficients)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "base_t": self.base_t,
            "q_coefficients": list(self.q_coefficients),
            "e": self.e,
            "d": self.d,
            "h_sign": self.h_sign,
            "h_linear_roots": list(self.h_linear_roots),
            "h_positive_quadratics": [list(value) for value in self.h_positive_quadratics],
            "norm_squareclass": self.expected_norm_squareclass,
        }


@dataclass(frozen=True)
class NestedCharacterLiftCandidate:
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


def _convolve(left: Sequence[int], right: Sequence[int]) -> list[int]:
    output = [0] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            output[i + j] += int(a) * int(b)
    return output


def build_nested_character_lift(
    *,
    family: str,
    base_t: int,
    target_t: int,
    q_coefficients: Sequence[int],
    e: int,
    d: int,
    expected_norm_squareclass: int,
    h_linear_roots: Sequence[int],
    h_positive_quadratics: Sequence[tuple[int, int]] = (),
    h_sign: int = 1,
) -> NestedCharacterLiftSpec:
    """Build a deterministic integral quadratic lift.

    A positive quadratic entry ``(center, radius)`` represents
    ``(y-center)^2 + radius^2``.  It changes the Kummer generator without
    changing its sign on a real quotient root.
    """

    q = tuple(int(value) for value in q_coefficients)
    if len(q) != 4 or q[-1] != 1:
        raise ValueError("q must be a monic integral cubic")
    if int(d) <= 0:
        raise ValueError("d must be positive")
    if int(h_sign) not in {-1, 1}:
        raise ValueError("h_sign must be -1 or 1")

    inner = _convolve(q, q)
    inner[0] -= int(e)
    base = _convolve(inner, inner)
    base[0] -= int(d)
    if len(base) != 13 or base[-1] != 1:
        raise AssertionError("nested quotient is not monic of degree twelve")

    h = [int(h_sign)]
    roots = tuple(int(value) for value in h_linear_roots)
    for root in roots:
        h = _convolve(h, (-root, 1))
    quadratics: list[tuple[int, int]] = []
    for raw_center, raw_radius in h_positive_quadratics:
        center, radius = int(raw_center), int(raw_radius)
        if radius <= 0:
            raise ValueError("positive-quadratic radii must be positive")
        quadratics.append((center, radius))
        h = _convolve(h, (center * center + radius * radius, -2 * center, 1))

    base_expression = sum(coefficient * _Y**power for power, coefficient in enumerate(base))
    h_expression = sum(coefficient * _Y**power for power, coefficient in enumerate(h))
    lifted = Poly(resultant(base_expression, _X**2 - h_expression, _Y), _X, domain="ZZ")
    coefficients = tuple(int(value) for value in reversed(lifted.all_coeffs()))
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise AssertionError("relative quadratic resultant is not monic of degree 24")

    return NestedCharacterLiftSpec(
        family=str(family),
        base_t=int(base_t),
        target_t=int(target_t),
        expected_norm_squareclass=int(expected_norm_squareclass),
        q_coefficients=q,
        e=int(e),
        d=int(d),
        h_sign=int(h_sign),
        h_linear_roots=roots,
        h_positive_quadratics=tuple(quadratics),
        base_coefficients=tuple(base),
        h_coefficients=tuple(h),
        coefficients=coefficients,
    )


def certify_nested_character_lift(
    spec: NestedCharacterLiftSpec,
    *,
    expected_root_count: int,
    gp: str = DEFAULT_GP,
    gap: str = DEFAULT_GAP,
    timeout: float = 1200,
    prime_limit: int = 10000,
    coefficient_limit: int = 10**55,
) -> NestedCharacterLiftCandidate:
    """Certify arithmetic, signature, character, and the full target group."""

    base = polynomial_expression(spec.base_coefficients, "x")
    h = polynomial_expression(spec.h_coefficients, "x")
    polynomial = polynomial_expression(spec.coefficients, "x")
    started = time.perf_counter()
    script = f"""
f={base}; h={h}; p={polynomial};
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(polsturm(f)!=12,print("ERROR|base-not-totally-real");quit(1));
if(!polisirreducible(p),print("ERROR|lift-reducible");quit(1));
print("ROOTS|",polsturm(p));
print("NORM_CORE|",core(polresultant(f,h)));
print("NFDISC|",abs(nfdisc(p))); quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    values = {
        tag: value
        for line in output.splitlines()
        if "|" in line
        for tag, value in [line.strip().split("|", 1)]
        if tag in {"ERROR", "ROOTS", "NORM_CORE", "NFDISC"}
    }
    if "ERROR" in values:
        raise ValueError(values["ERROR"])
    roots = int(values["ROOTS"])
    if roots != int(expected_root_count):
        raise ValueError(f"signature mismatch: expected {expected_root_count}, got {roots}")
    norm_core = int(values["NORM_CORE"])
    if norm_core != spec.expected_norm_squareclass:
        raise ValueError(
            "Kummer character mismatch: expected squareclass "
            f"{spec.expected_norm_squareclass}, got {norm_core}"
        )
    if max(abs(value) for value in spec.coefficients) >= coefficient_limit:
        raise ValueError("coefficient limit exceeded")

    excluded, witnesses = prove_full_target_group(
        spec.coefficients,
        spec.target_t,
        gap=gap,
        prime_limit=prime_limit,
        timeout=timeout,
    )
    field_disc = int(values["NFDISC"])
    line = spec.line
    parameter_key = candidate_hash(line)[:20]
    lineage = f"nested-character-lift:12T{spec.base_t}:24T{spec.target_t}"
    return NestedCharacterLiftCandidate(
        coefficients=line,
        candidate_hash=candidate_hash(line),
        local_root_count=roots,
        local_irreducible=True,
        target_t=spec.target_t,
        target_r=roots,
        label_probability=1.0,
        valid_probability=1.0,
        recipe_family=spec.family,
        recipe_lineage=lineage,
        recipe_id=f"{lineage}:{parameter_key}",
        construction_overgroup=f"C2^11_character_twist_over_12T{spec.base_t}",
        parameters=spec.parameters,
        base_coefficients=spec.base_coefficients,
        h_coefficients=spec.h_coefficients,
        norm_squareclass=norm_core,
        expected_norm_squareclass=spec.expected_norm_squareclass,
        exact_compatibility_proven=True,
        maximal_subgroups_excluded=tuple(excluded),
        modular_witnesses=tuple(witnesses),
        field_disc_abs=field_disc,
        estimated_nfdisc_abs=field_disc,
        submission_ready=True,
        cpu_ms=(time.perf_counter() - started) * 1000.0,
    )


def prove_full_target_group(
    coefficients: Sequence[int],
    target_t: int,
    *,
    gap: str = DEFAULT_GAP,
    gp: str = DEFAULT_GP,
    prime_limit: int = 10000,
    timeout: float = 1200,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Exclude every transitive maximal subgroup using Frobenius types.

    Irreducibility, checked before this function, excludes the intransitive
    maximal subgroups.  The character construction places the group inside
    ``24Ttarget_t``.  A factorization type missing from a maximal subgroup
    excludes every conjugate of that subgroup.
    """

    target_types, maximal_types = _target_cycle_data(int(target_t), gap, timeout)
    remaining = set(maximal_types)
    witnesses: list[dict[str, Any]] = []
    for prime, degrees in _pari_frobenius_types(
        coefficients,
        gp=gp,
        prime_limit=prime_limit,
        timeout=timeout,
    ):
        cycle_type = ".".join(map(str, sorted(degrees)))
        if cycle_type not in target_types:
            raise ValueError(
                f"Frobenius type {cycle_type} at {prime} is outside 24T{target_t}"
            )
        hit = sorted(name for name in remaining if cycle_type not in maximal_types[name])
        if hit:
            witnesses.append({
                "prime": int(prime),
                "cycle_type": cycle_type,
                "excludes": hit,
            })
            remaining.difference_update(hit)
        if not remaining:
            break
    if remaining:
        raise ValueError(
            f"maximal-subgroup proof incomplete for 24T{target_t}: {sorted(remaining)}"
        )
    return sorted(maximal_types), witnesses


def classify_transitive_subgroup(
    coefficients: Sequence[int],
    starting_target_t: int,
    *,
    gap: str = DEFAULT_GAP,
    gp: str = DEFAULT_GP,
    prime_limit: int = 10000,
    timeout: float = 1200,
    maximum_depth: int = 12,
    frobenius: Sequence[tuple[int, Sequence[int]]] | None = None,
) -> tuple[int, list[str], list[dict[str, Any]], list[int]]:
    """Descend a known overgroup through compatible transitive maximals.

    The caller must already know that the polynomial's Galois group is a
    subgroup of ``24Tstarting_target_t`` (for example from an exact Kummer
    norm character).  Irreducibility places it in a transitive maximal when
    it is proper.  Frobenius types then eliminate incompatible maximal
    subgroup classes recursively.  A unique surviving terminal is therefore
    the exact transitive group; ambiguous descents are rejected.

    The returned tuple contains the terminal T-number, its usual
    maximal-subgroup certificate, and the overgroup-to-terminal T-number
    chain.
    """

    frobenius_rows = (
        [(int(prime), [int(value) for value in degrees]) for prime, degrees in frobenius]
        if frobenius is not None
        else _pari_frobenius_types(
            coefficients,
            gp=gp,
            prime_limit=prime_limit,
            timeout=timeout,
        )
    )
    observed = [
        (prime, ".".join(map(str, sorted(degrees))))
        for prime, degrees in frobenius_rows
    ]
    memo: dict[tuple[int, int], list[tuple[int, list[str], list[dict[str, Any]], list[int]]]] = {}

    def descend(
        target_t: int,
        chain: list[int],
        depth: int,
    ) -> list[tuple[int, list[str], list[dict[str, Any]], list[int]]]:
        if depth > maximum_depth:
            return []
        key = int(target_t), depth
        if key in memo:
            return [
                (terminal, excluded, witnesses, chain + suffix[1:])
                for terminal, excluded, witnesses, suffix in memo[key]
            ]
        target_types, maximal_types = _target_cycle_data(int(target_t), gap, timeout)
        if any(cycle_type not in target_types for _, cycle_type in observed):
            return []
        compatible = [
            name
            for name, cycle_types in maximal_types.items()
            if all(cycle_type in cycle_types for _, cycle_type in observed)
        ]
        if not compatible:
            remaining = set(maximal_types)
            witnesses: list[dict[str, Any]] = []
            for prime, cycle_type in observed:
                hit = sorted(
                    name
                    for name in remaining
                    if cycle_type not in maximal_types[name]
                )
                if hit:
                    witnesses.append({
                        "prime": int(prime),
                        "cycle_type": cycle_type,
                        "excludes": hit,
                    })
                    remaining.difference_update(hit)
                if not remaining:
                    break
            if remaining:
                return []
            result = [(int(target_t), sorted(maximal_types), witnesses, list(chain))]
            memo[key] = [
                (terminal, excluded, witness_rows, [int(target_t)])
                for terminal, excluded, witness_rows, _ in result
            ]
            return result

        child_targets: set[int] = set()
        for name in compatible:
            match = re.search(r":24T(\d+)$", name)
            if match:
                child_targets.add(int(match.group(1)))
        terminals: list[tuple[int, list[str], list[dict[str, Any]], list[int]]] = []
        for child in sorted(child_targets):
            if child in chain:
                continue
            terminals.extend(descend(child, chain + [child], depth + 1))
        # Multiple conjugacy classes with the same transitive identification
        # are harmless.  Distinct terminal T-numbers are genuinely ambiguous.
        by_terminal: dict[int, tuple[int, list[str], list[dict[str, Any]], list[int]]] = {}
        for result in terminals:
            current = by_terminal.get(result[0])
            if current is None or len(result[3]) < len(current[3]):
                by_terminal[result[0]] = result
        return list(by_terminal.values())

    results = descend(
        int(starting_target_t),
        [int(starting_target_t)],
        0,
    )
    unique = {result[0]: result for result in results}
    if not unique:
        raise ValueError(
            f"no compatible transitive subgroup below 24T{starting_target_t}"
        )
    if len(unique) != 1:
        raise ValueError(
            "ambiguous transitive subgroup descent below "
            f"24T{starting_target_t}: {sorted(unique)}"
        )
    return next(iter(unique.values()))


def _pari_frobenius_types(
    coefficients: Sequence[int],
    *,
    gp: str = DEFAULT_GP,
    prime_limit: int = 10000,
    timeout: float = 1200,
) -> list[tuple[int, list[int]]]:
    """Factor a polynomial modulo every unramified prime using PARI.

    SymPy's finite-field factorizer becomes disproportionately slow at the
    larger primes needed by a few maximal-subgroup witnesses.  PARI batches
    the same exact computation in a fraction of the time.
    """

    polynomial = polynomial_expression(coefficients, "x")
    script = f"""
x='x; p={polynomial}; ell=2;
while(ell<={int(prime_limit)},pm=p*Mod(1,ell);if(poldegree(gcd(pm,deriv(pm)))==0,fa=factor(pm);d=vecsort(vector(matsize(fa)[1],i,poldegree(lift(fa[i,1]))));print("FROB|",ell,"|",d));ell=nextprime(ell+1));quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    rows: list[tuple[int, list[int]]] = []
    for line in output.splitlines():
        if not line.startswith("FROB|"):
            continue
        _, raw_prime, raw_degrees = line.split("|", 2)
        degrees = ast.literal_eval(raw_degrees)
        if not isinstance(degrees, list):
            raise RuntimeError(f"invalid PARI Frobenius vector: {raw_degrees}")
        rows.append((int(raw_prime), [int(value) for value in degrees]))
    if not rows:
        raise RuntimeError("PARI produced no unramified Frobenius types")
    return rows


@lru_cache(maxsize=None)
def _target_cycle_data(
    target_t: int,
    gap: str,
    timeout: float,
) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    if not shutil.which(gap) and not Path(gap).exists():
        raise FileNotFoundError(f"GAP executable not found: {gap}")
    script = f'''\
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);;
CycleKey := p -> JoinStringsWithSeparator(
  List(SortedList(CycleLengths(p,[1..24])),String), ".");;
G := TransitiveGroup(24,{int(target_t)});;
for cl in ConjugacyClasses(G) do
  Print("TARGET|",CycleKey(Representative(cl)),"\\n");
od;
maximals := MaximalSubgroupClassReps(G);;
for i in [1..Length(maximals)] do
  H := maximals[i];
  if IsTransitive(H,[1..24]) then
    Print("MAX|",i,"|",TransitiveIdentification(H),"\\n");
    for cl in ConjugacyClasses(H) do
      Print("TYPE|",i,"|",CycleKey(Representative(cl)),"\\n");
    od;
  fi;
od;
QUIT;
'''
    completed = subprocess.run(
        [gap, "-q", "-A"],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"GAP failed: {completed.stderr or completed.stdout}")
    target_types: set[str] = set()
    names: dict[str, str] = {}
    types_by_index: dict[str, set[str]] = {}
    for line in completed.stdout.splitlines():
        if line.startswith("TARGET|"):
            target_types.add(line.split("|", 1)[1])
        elif line.startswith("MAX|"):
            _, index, transitive_id = line.split("|", 2)
            names[index] = f"max#{index}:24T{transitive_id}"
            types_by_index[index] = set()
        elif line.startswith("TYPE|"):
            _, index, cycle_type = line.split("|", 2)
            types_by_index[index].add(cycle_type)
    # Some transitive groups have no proper transitive maximal subgroup.
    # In that case irreducibility plus known containment already forces the
    # full target, so an empty maximal list is a valid terminal certificate.
    if not target_types:
        raise RuntimeError(f"incomplete GAP maximal-subgroup output for 24T{target_t}")
    return (
        frozenset(target_types),
        {names[index]: frozenset(types_by_index[index]) for index in names},
    )
