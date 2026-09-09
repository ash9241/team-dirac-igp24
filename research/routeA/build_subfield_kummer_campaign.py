#!/usr/bin/env python3
"""Build exact degree-24 Kummer lifts from proper subfields.

For a totally real degree-12 field ``K`` and a proper subfield ``L``, an
element of ``L`` has only ``[L:Q]`` distinct conjugates.  The squareclass
orbit of that element inside ``K`` is therefore much smaller than the orbit
of a generic linear seed.  Quadratic lifts by these elements directly target
the low-dimensional Kummer modules that ordinary character searches miss.

The full ``C2 wr 12T`` group is an exact overgroup.  Every emitted candidate
is descended through transitive maximal subgroups using exact Frobenius
witnesses; ambiguous descents are rejected.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

from routeA.build_catalog_character_campaign import load_jsonl
from routeA.constructions.compositum_8x3 import (
    DEFAULT_GP,
    _run_gp,
    polynomial_expression,
)
from routeA.constructions.integral_basis_character_lift import (
    IntegralBasisGeneratorOption,
    build_integral_basis_lift,
    family_from_integral_basis_seed,
)
from routeA.constructions.nested_character_lift import (
    DEFAULT_GAP,
    _pari_frobenius_types,
    classify_transitive_subgroup,
)
from routeA.ledger import candidate_hash


DATA = Path(__file__).resolve().parent / "data"
DEFAULT_CATALOG = DATA / "degree12_lmfdb_catalog.jsonl"
DEFAULT_WREATH_MAP = DATA / "full_wreath_map.jsonl"


def _trim(values: Iterable[Fraction]) -> tuple[Fraction, ...]:
    output = [Fraction(value) for value in values]
    while len(output) > 1 and not output[-1]:
        output.pop()
    return tuple(output)


def _fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def extract_subfield_seeds(
    base_coefficients: Sequence[int],
    *,
    degrees: Sequence[int],
    shifts: range,
    gp: str = DEFAULT_GP,
    timeout: float = 1200,
) -> list[dict[str, Any]]:
    """Return power-basis coordinates of shifted proper-subfield generators."""

    base = polynomial_expression(base_coefficients, "x")
    degree_values = ",".join(str(int(value)) for value in degrees)
    shift_values = ",".join(str(int(value)) for value in shifts)
    script = f"""
x='x; f={base}; ds=[{degree_values}]; cs=[{shift_values}];
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(polsturm(f)!=12,print("ERROR|base-not-totally-real");quit(1));
b=bnfinit(f);
for(di=1,#ds,d=ds[di];S=nfsubfields(f,d);for(j=1,#S,e=lift(Mod(S[j][2],f));for(ci=1,#cs,c=cs[ci];h=lift(Mod(e-c,f));if(h!=0,em=nfeltembed(b,h);r=2*sum(k=1,12,real(em[k])>0);n=nfeltnorm(b,Mod(h,f));nc=core(numerator(n)*denominator(n));vn=vector(12,k,numerator(polcoef(h,k-1)));vd=vector(12,k,denominator(polcoef(h,k-1)));print("SEED|",d,"|",j,"|",c,"|",r,"|",nc,"|",vn,"|",vd))))); quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    if "ERROR|" in output:
        error = next(line for line in output.splitlines() if line.startswith("ERROR|"))
        raise ValueError(error.split("|", 1)[1])
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.startswith("SEED|"):
            continue
        parts = line.split("|", 7)
        if len(parts) != 8:
            raise ValueError(f"invalid PARI subfield seed row: {line}")
        _, raw_degree, raw_index, raw_shift, raw_roots, raw_norm, raw_num, raw_den = parts
        numerators = ast.literal_eval(raw_num)
        denominators = ast.literal_eval(raw_den)
        coefficients = _trim(
            Fraction(int(num), int(den))
            for num, den in zip(numerators, denominators)
        )
        rows.append({
            "subfield_degree": int(raw_degree),
            "subfield_index": int(raw_index),
            "shift": int(raw_shift),
            "target_r": int(raw_roots),
            "norm_squareclass": int(raw_norm),
            "seed_coefficients": coefficients,
        })
    return rows


def _field_discriminant(
    coefficients: Sequence[int],
    *,
    gp: str,
    timeout: float,
) -> int:
    polynomial = polynomial_expression(coefficients, "x")
    output = _run_gp(
        f"x='x; p={polynomial}; print(\"NFDISC|\",abs(nfdisc(p))); quit;",
        gp=gp,
        timeout=timeout,
    )
    for line in output.splitlines():
        if line.startswith("NFDISC|"):
            return int(line.split("|", 1)[1])
    raise RuntimeError("PARI did not return a field discriminant")


def build_subfield_candidates(
    *,
    catalog_path: str | Path,
    wreath_map_path: str | Path,
    degrees: Sequence[int] = (2, 3, 4, 6),
    shift_min: int = -2,
    shift_max: int = 2,
    coefficient_limit: int = 10**120,
    prime_limit: int = 30000,
    gp: str = DEFAULT_GP,
    gap: str = DEFAULT_GAP,
    timeout: float = 1200,
    limit_fields: int | None = None,
    classification_mode: str = "exact",
    bases: Sequence[int] = (),
    source_signatures: Sequence[int] = (),
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if shift_min > shift_max:
        raise ValueError("shift_min must not exceed shift_max")
    requested_degrees = tuple(sorted({int(value) for value in degrees}))
    if not requested_degrees or requested_degrees[0] < 2 or requested_degrees[-1] >= 12:
        raise ValueError("proper subfield degrees must lie in [2, 11]")
    if classification_mode not in {"exact", "validity"}:
        raise ValueError("classification_mode must be exact or validity")
    wreath_targets = {
        int(row["base_t"]): int(row["target_t"])
        for row in load_jsonl(wreath_map_path)
    }
    wanted_bases = {int(value) for value in bases}
    wanted_signatures = {int(value) for value in source_signatures}
    fields = load_jsonl(catalog_path)
    if wanted_bases:
        fields = [field for field in fields if int(field["base_t"]) in wanted_bases]
    if wanted_signatures:
        fields = [
            field for field in fields
            if int(field.get("source_signature", -1)) in wanted_signatures
        ]
    if limit_fields is not None:
        fields = fields[: max(0, int(limit_fields))]
    counts: Counter[str] = Counter()
    unique: dict[str, dict[str, Any]] = {}
    for field in fields:
        counts["fields"] += 1
        base_t = int(field["base_t"])
        starting_target_t = wreath_targets.get(base_t)
        if starting_target_t is None:
            counts["fields_without_wreath_map"] += 1
            continue
        base_coefficients = tuple(int(value) for value in field["coefficients"])
        try:
            seeds = extract_subfield_seeds(
                base_coefficients,
                degrees=requested_degrees,
                shifts=range(int(shift_min), int(shift_max) + 1),
                gp=gp,
                timeout=timeout,
            )
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            counts["subfield_extraction_failures"] += 1
            continue
        counts["subfield_seeds"] += len(seeds)
        for seed in seeds:
            family = family_from_integral_basis_seed(
                family=(
                    f"subfield_kummer_12t{base_t}_d{seed['subfield_degree']}_"
                    f"j{seed['subfield_index']}_c{seed['shift']}"
                ),
                base_t=base_t,
                target_t=starting_target_t,
                expected_norm_squareclass=int(seed["norm_squareclass"]),
                base_coefficients=base_coefficients,
                seed_coefficients=seed["seed_coefficients"],
                target_root_counts=(int(seed["target_r"]),),
                construction_overgroup=f"full_C2_wreath_over_12T{base_t}",
                character_name="proper_subfield_squareclass_orbit",
                base_root_count=12,
            )
            option = IntegralBasisGeneratorOption(
                target_r=int(seed["target_r"]),
                height=max(
                    max(abs(value.numerator), value.denominator)
                    for value in seed["seed_coefficients"]
                ),
                unit_mask=0,
                unit_sign=1,
                h_coefficients=tuple(seed["seed_coefficients"]),
            )
            try:
                spec = build_integral_basis_lift(
                    family,
                    option,
                    gp=gp,
                    timeout=timeout,
                    coefficient_limit=int(coefficient_limit),
                    absolute_reduction=classification_mode == "exact",
                )
                if classification_mode == "exact":
                    frobenius = _pari_frobenius_types(
                        spec.coefficients,
                        gp=gp,
                        prime_limit=int(prime_limit),
                        timeout=timeout,
                    )
                    terminal_t, excluded, witnesses, chain = classify_transitive_subgroup(
                        spec.coefficients,
                        starting_target_t,
                        gap=gap,
                        gp=gp,
                        prime_limit=int(prime_limit),
                        timeout=timeout,
                        frobenius=frobenius,
                    )
                else:
                    terminal_t = int(starting_target_t)
                    excluded = []
                    witnesses = []
                    chain = [int(starting_target_t)]
                field_disc_abs = (
                    _field_discriminant(
                        spec.coefficients,
                        gp=gp,
                        timeout=timeout,
                    )
                    if classification_mode == "exact"
                    else 0
                )
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                counts["candidate_failures"] += 1
                continue
            line = spec.line
            key = candidate_hash(line)
            row = {
                "coefficients": line,
                "candidate_hash": key,
                "local_root_count": int(seed["target_r"]),
                "local_irreducible": True,
                "target_t": int(terminal_t),
                "target_r": int(seed["target_r"]),
                "label_probability": 1.0 if classification_mode == "exact" else 0.0,
                "valid_probability": 1.0,
                "recipe_family": family.family,
                "recipe_lineage": f"subfield-kummer:12T{base_t}:24T{terminal_t}",
                "recipe_id": f"subfield-kummer:{key[:20]}",
                "construction_overgroup": f"full_C2_wreath_over_12T{base_t}",
                "parameters": {
                    "base_t": base_t,
                    "target_t": int(terminal_t),
                    "subfield_degree": int(seed["subfield_degree"]),
                    "subfield_index": int(seed["subfield_index"]),
                    "subfield_shift": int(seed["shift"]),
                    "overgroup_chain": [int(value) for value in chain],
                    "norm_squareclass": int(seed["norm_squareclass"]),
                },
                "base_t": base_t,
                "base_coefficients": list(base_coefficients),
                "h_coefficients": [
                    _fraction_text(value) for value in seed["seed_coefficients"]
                ],
                "norm_squareclass": int(seed["norm_squareclass"]),
                "expected_norm_squareclass": int(seed["norm_squareclass"]),
                "exact_compatibility_proven": classification_mode == "exact",
                "maximal_subgroups_excluded": list(excluded),
                "modular_witnesses": list(witnesses),
                "field_disc_abs": int(field_disc_abs),
                "estimated_nfdisc_abs": int(field_disc_abs),
                "submission_ready": classification_mode == "exact",
                "exploration_ready": classification_mode == "validity",
                "source_field_label": str(field.get("label") or ""),
                "source_field_disc_abs": int(field.get("disc_abs") or 0),
            }
            previous = unique.get(key)
            if previous is None or int(row["field_disc_abs"]) < int(previous["field_disc_abs"]):
                unique[key] = row
            counts[
                "certified_candidates"
                if classification_mode == "exact"
                else "validity_candidates"
            ] += 1
    rows = sorted(
        unique.values(),
        key=lambda row: (
            int(row["target_t"]),
            int(row["target_r"]),
            int(row["field_disc_abs"]),
            str(row["candidate_hash"]),
        ),
    )
    counts["unique_candidates"] = len(rows)
    counts["target_groups"] = len({int(row["target_t"]) for row in rows})
    return rows, dict(sorted(counts.items()))


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--wreath-map", type=Path, default=DEFAULT_WREATH_MAP)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--degree", action="append", type=int, default=[])
    parser.add_argument("--shift-min", type=int, default=-2)
    parser.add_argument("--shift-max", type=int, default=2)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=30000)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--limit-fields", type=int)
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--source-signature", action="append", type=int, default=[])
    parser.add_argument(
        "--classification-mode",
        choices=("exact", "validity"),
        default="exact",
    )
    args = parser.parse_args()
    rows, counts = build_subfield_candidates(
        catalog_path=args.catalog,
        wreath_map_path=args.wreath_map,
        degrees=args.degree or (2, 3, 4, 6),
        shift_min=args.shift_min,
        shift_max=args.shift_max,
        coefficient_limit=args.coefficient_limit,
        prime_limit=args.prime_limit,
        timeout=args.timeout,
        limit_fields=args.limit_fields,
        classification_mode=args.classification_mode,
        bases=args.base,
        source_signatures=args.source_signature,
    )
    _write_jsonl(args.manifest, rows)
    args.payload.parent.mkdir(parents=True, exist_ok=True)
    args.payload.write_text(
        "".join(str(row["coefficients"]) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps({
        "manifest": str(args.manifest),
        "payload": str(args.payload),
        **counts,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
