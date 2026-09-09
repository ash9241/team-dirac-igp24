#!/usr/bin/env python3
"""Build validity-certified Kummer lifts from products of subfield seeds.

A seed pulled back from one proper subfield only sees one small permutation
module.  Products of seeds from distinct proper subfields add those modules
over F2 and reach the intermediate Kummer architectures missed by both the
single-subfield and full-character searches.

The input is one or more candidate manifests produced by
``build_subfield_kummer_campaign``.  Products are formed in the degree-12
quotient field, reduced modulo its defining polynomial, and converted to
integral degree-24 equations with PARI.  Every emitted row has a local proof
of degree, irreducibility, signature, norm squareclass, and field
discriminant.  Group labels deliberately remain server-authoritative.
"""

from __future__ import annotations

import argparse
import ast
import itertools
import json
import subprocess
from collections import Counter, defaultdict
from fractions import Fraction
from math import gcd
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.constructions.compositum_8x3 import (
    DEFAULT_GP,
    _run_gp,
    polynomial_expression,
)
from routeA.ledger import candidate_hash, canonical_coefficients


DATA = Path(__file__).resolve().parent / "data"
DEFAULT_WREATH_MAP = DATA / "full_wreath_map.jsonl"


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _squarefree_product(left: int, right: int) -> int:
    common = gcd(abs(int(left)), abs(int(right)))
    return int(left) * int(right) // (common * common)


def _multiply_mod(
    left: Sequence[int | str | Fraction],
    right: Sequence[int | str | Fraction],
    modulus: Sequence[int | str | Fraction],
) -> tuple[Fraction, ...]:
    """Multiply ascending rational polynomials modulo a monic modulus."""

    degree = len(modulus) - 1
    rational_modulus = tuple(Fraction(value) for value in modulus)
    if degree <= 0 or rational_modulus[-1] != 1:
        raise ValueError("modulus must be monic of positive degree")
    output = [Fraction(0)] * (len(left) + len(right) - 1)
    for left_index, left_value in enumerate(left):
        for right_index, right_value in enumerate(right):
            output[left_index + right_index] += Fraction(left_value) * Fraction(right_value)
    output.extend([Fraction(0)] * max(0, degree + 1 - len(output)))
    for power in range(len(output) - 1, degree - 1, -1):
        leading = output[power]
        if not leading:
            continue
        offset = power - degree
        for index in range(degree + 1):
            output[offset + index] -= leading * rational_modulus[index]
    reduced = output[:degree]
    while len(reduced) > 1 and not reduced[-1]:
        reduced.pop()
    return tuple(reduced)


def _fraction_text(value: Fraction) -> str:
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _fractional_polynomial_expression(
    coefficients: Sequence[int | str | Fraction],
    variable: str,
) -> str:
    terms: list[str] = []
    for power, raw_value in enumerate(coefficients):
        value = Fraction(raw_value)
        if not value:
            continue
        scalar = _fraction_text(value)
        if power == 0:
            term = scalar
        elif power == 1:
            term = f"({scalar})*{variable}"
        else:
            term = f"({scalar})*{variable}^{power}"
        terms.append(term)
    return "+".join(terms) or "0"


def _factor_structure(row: Mapping[str, Any]) -> tuple[int, int]:
    parameters = row.get("parameters") or {}
    return int(parameters["subfield_degree"]), int(parameters["subfield_index"])


def _factor_key(row: Mapping[str, Any]) -> tuple[int, int, int]:
    parameters = row.get("parameters") or {}
    degree, index = _factor_structure(row)
    return degree, index, int(parameters["subfield_shift"])


def _factor_height(row: Mapping[str, Any]) -> int:
    values = tuple(Fraction(value) for value in row["h_coefficients"])
    return max(max(abs(value.numerator), value.denominator) for value in values)


def _architecture_novelty(factors: Sequence[Mapping[str, Any]]) -> int:
    """Heuristically favor factors whose subfields can generate all of K.

    In the degree-12 fields at issue, cubic subfields are commonly contained
    in sextic ones, so a 3x6 product often collapses back to the already-seen
    sextic lane.  A 3x4 or 4x6 pair, or two distinct sextic subfields, is much
    more likely to generate the full quotient while retaining a non-generic
    F2 module.  This ordering only affects compute allocation; every output
    still receives the same arithmetic validity proof.
    """

    structures = {_factor_structure(row) for row in factors}
    degrees = {degree for degree, _ in structures}
    sextic_indices = {index for degree, index in structures if degree == 6}
    if {3, 4} <= degrees or {4, 6} <= degrees or len(sextic_indices) >= 2:
        return 3
    if len(structures) >= 3:
        return 2
    if len(degrees) >= 2:
        return 1
    return 0


def enumerate_product_seeds(
    rows: Sequence[Mapping[str, Any]],
    *,
    factor_counts: Sequence[int] = (2,),
    min_distinct_structures: int = 2,
    max_abs_shift: int | None = 2,
    max_products: int | None = 250,
) -> list[dict[str, Any]]:
    """Return ranked, unique products for one degree-12 source field."""

    requested_counts = tuple(sorted({int(value) for value in factor_counts}))
    if not requested_counts or requested_counts[0] < 2:
        raise ValueError("factor counts must be at least two")
    if int(min_distinct_structures) < 2:
        raise ValueError("min_distinct_structures must be at least two")
    if not rows:
        return []
    base = tuple(int(value) for value in rows[0]["base_coefficients"])
    if len(base) != 13 or base[-1] != 1:
        raise ValueError("source base polynomial must be monic of degree twelve")
    for row in rows:
        if tuple(int(value) for value in row["base_coefficients"]) != base:
            raise ValueError("product factors must belong to one source field")

    # Keep one compact representative for each shifted subfield generator.
    atoms_by_key: dict[tuple[int, int, int], Mapping[str, Any]] = {}
    for row in rows:
        key = _factor_key(row)
        if max_abs_shift is not None and abs(key[2]) > int(max_abs_shift):
            continue
        previous = atoms_by_key.get(key)
        if previous is None or _factor_height(row) < _factor_height(previous):
            atoms_by_key[key] = row
    atoms = [atoms_by_key[key] for key in sorted(atoms_by_key)]

    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    seen_seeds: set[tuple[Fraction, ...]] = set()
    for factor_count in requested_counts:
        for factors in itertools.combinations(atoms, factor_count):
            structures = tuple(_factor_structure(row) for row in factors)
            if len(set(structures)) < int(min_distinct_structures):
                continue
            seed: tuple[Fraction, ...] = (Fraction(1),)
            norm_squareclass = 1
            factor_rows: list[dict[str, Any]] = []
            for row in factors:
                seed = _multiply_mod(seed, row["h_coefficients"], base)
                norm_squareclass = _squarefree_product(
                    norm_squareclass,
                    int(row["norm_squareclass"]),
                )
                degree, index, shift = _factor_key(row)
                factor_rows.append({
                    "subfield_degree": degree,
                    "subfield_index": index,
                    "subfield_shift": shift,
                    "norm_squareclass": int(row["norm_squareclass"]),
                    "candidate_hash": str(row.get("candidate_hash") or ""),
                })
            if not any(seed) or seed in seen_seeds:
                continue
            seen_seeds.add(seed)
            rank = (
                -_architecture_novelty(factors),
                -len(set(structures)),
                sum(abs(_factor_key(row)[2]) for row in factors),
                max(_factor_height(row) for row in factors),
                factor_count,
                tuple(_factor_key(row) for row in factors),
            )
            ranked.append((rank, {
                "seed_coefficients": seed,
                "norm_squareclass": int(norm_squareclass),
                "factors": factor_rows,
            }))
    ranked.sort(key=lambda value: value[0])
    seeds = [row for _, row in ranked]
    if max_products is not None:
        seeds = seeds[: max(0, int(max_products))]
    return seeds


def build_product_lifts(
    base_coefficients: Sequence[int],
    seeds: Sequence[Mapping[str, Any]],
    *,
    gp: str = DEFAULT_GP,
    timeout: float = 3600,
    coefficient_limit: int = 10**120,
    absolute_reduction: bool = True,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Build many product lifts while reusing one expensive ``bnfinit``."""

    counts: Counter[str] = Counter()
    if not seeds:
        return [], counts
    base = polynomial_expression(base_coefficients, "t")
    expressions = ",".join(
        _fractional_polynomial_expression(seed["seed_coefficients"], "t")
        for seed in seeds
    )
    # PARI variable priority is declaration-order sensitive here: the
    # relative variable ``z`` must precede the base-field variable ``t``.
    reduction = "polredabs(p)" if absolute_reduction else "polredbest(p)"
    discriminant = "abs(nfdisc(pr))" if absolute_reduction else "0"
    script = f"""
z='z; t='t; f={base};
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(polsturm(f)!=12,print("ERROR|base-not-totally-real");quit(1));
b=bnfinit(f); H=[{expressions}];
for(i=1,#H,h=lift(Mod(H[i],f));n=nfeltnorm(b,Mod(h,f));nc=core(numerator(n)*denominator(n));p=rnfequation(b,z^2-Mod(h,f));if(poldegree(p)!=24,print("SKIP|",i,"|degree");next);if(!polisirreducible(p),print("SKIP|",i,"|reducible");next);pr={reduction};r=polsturm(pr);d={discriminant};print("CAND|",i,"|",r,"|",nc,"|",d,"|",Vecrev(Vec(pr))));quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    if "ERROR|" in output:
        error = next(line for line in output.splitlines() if line.startswith("ERROR|"))
        raise ValueError(error.split("|", 1)[1])
    built: list[dict[str, Any]] = []
    for line in output.splitlines():
        if line.startswith("SKIP|"):
            _, _, reason = line.split("|", 2)
            counts[f"skipped_{reason}"] += 1
            continue
        if not line.startswith("CAND|"):
            continue
        _, raw_index, raw_roots, raw_norm, raw_disc, raw_vector = line.split("|", 5)
        index = int(raw_index) - 1
        if index < 0 or index >= len(seeds):
            raise ValueError(f"PARI returned invalid product index {raw_index}")
        source = seeds[index]
        norm_squareclass = int(raw_norm)
        if norm_squareclass != int(source["norm_squareclass"]):
            raise ValueError(
                f"product norm mismatch: expected {source['norm_squareclass']}, got {norm_squareclass}"
            )
        coefficients = tuple(int(value) for value in ast.literal_eval(raw_vector))
        if len(coefficients) != 25 or coefficients[-1] != 1:
            raise ValueError("absolute product lift is not monic of degree twenty-four")
        if max(abs(value) for value in coefficients) >= int(coefficient_limit):
            counts["skipped_coefficient_limit"] += 1
            continue
        roots = int(raw_roots)
        if roots < 0 or roots > 24 or roots % 2:
            raise ValueError(f"invalid product-lift root count {roots}")
        built.append({
            **dict(source),
            "coefficients": coefficients,
            "target_r": roots,
            "field_disc_abs": int(raw_disc),
        })
        counts["validity_lifts"] += 1
    counts["missing_pari_rows"] += len(seeds) - counts["validity_lifts"] - sum(
        value for key, value in counts.items() if key.startswith("skipped_")
    )
    return built, counts


def build_subfield_product_candidates(
    *,
    source_paths: Sequence[str | Path],
    wreath_map_path: str | Path = DEFAULT_WREATH_MAP,
    factor_counts: Sequence[int] = (2,),
    min_distinct_structures: int = 2,
    max_abs_shift: int | None = 2,
    max_products_per_field: int | None = 250,
    coefficient_limit: int = 10**120,
    gp: str = DEFAULT_GP,
    timeout: float = 3600,
    bases: Sequence[int] = (),
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    wreath_targets = {
        int(row["base_t"]): int(row["target_t"])
        for row in load_jsonl(wreath_map_path)
    }
    wanted_bases = {int(value) for value in bases}
    grouped: dict[tuple[int, tuple[int, ...], str], list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for source_path in source_paths:
        for row in load_jsonl(source_path):
            counts["source_rows"] += 1
            base_t = int(row["base_t"])
            if wanted_bases and base_t not in wanted_bases:
                continue
            if base_t not in wreath_targets:
                counts["source_rows_without_wreath_map"] += 1
                continue
            key = (
                base_t,
                tuple(int(value) for value in row["base_coefficients"]),
                str(row.get("source_field_label") or ""),
            )
            grouped[key].append(row)
    counts["source_fields"] = len(grouped)

    unique: dict[str, dict[str, Any]] = {}
    for (base_t, base_coefficients, source_label), factor_rows in sorted(grouped.items()):
        seeds = enumerate_product_seeds(
            factor_rows,
            factor_counts=factor_counts,
            min_distinct_structures=min_distinct_structures,
            max_abs_shift=max_abs_shift,
            max_products=max_products_per_field,
        )
        counts["product_seeds"] += len(seeds)
        if not seeds:
            counts["fields_without_products"] += 1
            continue
        try:
            lifts, lift_counts = build_product_lifts(
                base_coefficients,
                seeds,
                gp=gp,
                timeout=timeout,
                coefficient_limit=coefficient_limit,
                absolute_reduction=False,
            )
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            counts["field_build_failures"] += 1
            continue
        counts.update(lift_counts)
        starting_target_t = int(wreath_targets[base_t])
        source_disc = min(
            (int(row.get("source_field_disc_abs") or 0) for row in factor_rows),
            default=0,
        )
        for lift in lifts:
            line = canonical_coefficients(lift["coefficients"])
            key = candidate_hash(line)
            factors = list(lift["factors"])
            factor_tag = "-".join(
                f"d{row['subfield_degree']}j{row['subfield_index']}c{row['subfield_shift']}"
                for row in factors
            )
            row = {
                "coefficients": line,
                "candidate_hash": key,
                "local_root_count": int(lift["target_r"]),
                "local_irreducible": True,
                "target_t": starting_target_t,
                "target_r": int(lift["target_r"]),
                "label_probability": 0.0,
                "valid_probability": 1.0,
                "recipe_family": f"subfield_product_12t{base_t}_{factor_tag}",
                "recipe_lineage": f"subfield-product-kummer:12T{base_t}:24T{starting_target_t}",
                "recipe_id": f"subfield-product-kummer:{key[:20]}",
                "construction_overgroup": f"full_C2_wreath_over_12T{base_t}",
                "parameters": {
                    "base_t": base_t,
                    "target_t": starting_target_t,
                    "factor_count": len(factors),
                    "distinct_subfield_structures": len({
                        (item["subfield_degree"], item["subfield_index"])
                        for item in factors
                    }),
                    "subfield_factors": factors,
                    "norm_squareclass": int(lift["norm_squareclass"]),
                    "overgroup_chain": [starting_target_t],
                },
                "base_t": base_t,
                "base_coefficients": list(base_coefficients),
                "h_coefficients": [
                    _fraction_text(value) for value in lift["seed_coefficients"]
                ],
                "norm_squareclass": int(lift["norm_squareclass"]),
                "expected_norm_squareclass": int(lift["norm_squareclass"]),
                "exact_compatibility_proven": False,
                "maximal_subgroups_excluded": [],
                "modular_witnesses": [],
                "field_disc_abs": int(lift["field_disc_abs"]),
                "estimated_nfdisc_abs": int(lift["field_disc_abs"]),
                "submission_ready": False,
                "exploration_ready": True,
                "source_field_label": source_label,
                "source_field_disc_abs": source_disc,
            }
            previous = unique.get(key)
            if previous is None or int(row["field_disc_abs"]) < int(previous["field_disc_abs"]):
                unique[key] = row
            counts["validity_candidates"] += 1
    rows = sorted(
        unique.values(),
        key=lambda row: (
            int(row["base_t"]),
            int(row["target_r"]),
            int(row["field_disc_abs"]),
            str(row["candidate_hash"]),
        ),
    )
    counts["unique_candidates"] = len(rows)
    counts["candidate_bases"] = len({int(row["base_t"]) for row in rows})
    return rows, dict(sorted(counts.items()))


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", type=Path, required=True)
    parser.add_argument("--wreath-map", type=Path, default=DEFAULT_WREATH_MAP)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--factor-count", action="append", type=int, default=[])
    parser.add_argument("--min-distinct-structures", type=int, default=2)
    parser.add_argument("--max-abs-shift", type=int, default=2)
    parser.add_argument("--max-products-per-field", type=int, default=250)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--base", action="append", type=int, default=[])
    args = parser.parse_args()
    rows, counts = build_subfield_product_candidates(
        source_paths=args.source,
        wreath_map_path=args.wreath_map,
        factor_counts=args.factor_count or (2,),
        min_distinct_structures=args.min_distinct_structures,
        max_abs_shift=args.max_abs_shift,
        max_products_per_field=args.max_products_per_field,
        coefficient_limit=args.coefficient_limit,
        timeout=args.timeout,
        bases=args.base,
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
