#!/usr/bin/env python3
"""Build Kummer lifts from proper-subfield seeds twisted by field units.

Raw subfield elements realize only a few permutation submodules.  Multiplying
one such seed by the 2^11 unit squareclasses of a totally real degree-12 field
perturbs its Galois orbit without changing the quotient field.  This targets
the intermediate kernel dimensions that remain absent from the single-seed,
product, and full-wreath lanes.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

from routeA.build_subfield_product_campaign import (
    DEFAULT_GP,
    DEFAULT_WREATH_MAP,
    _factor_height,
    _fraction_text,
    _fractional_polynomial_expression,
    build_product_lifts,
    load_jsonl,
)
from routeA.constructions.compositum_8x3 import _run_gp, polynomial_expression
from routeA.ledger import candidate_hash, canonical_coefficients


def _source_structure(row: Mapping[str, Any]) -> tuple[int, int]:
    parameters = row.get("parameters") or {}
    return int(parameters["subfield_degree"]), int(parameters["subfield_index"])


def select_source_seeds(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_source_seeds: int | None = None,
    generic_sources: bool = False,
) -> list[Mapping[str, Any]]:
    """Keep one compact, near-zero shift representative per subfield."""

    if generic_sources:
        selected = sorted(rows, key=lambda row: str(row.get("candidate_hash") or ""))
        if max_source_seeds is not None:
            selected = selected[: max(0, int(max_source_seeds))]
        return selected

    best: dict[tuple[int, int], Mapping[str, Any]] = {}
    for row in rows:
        key = _source_structure(row)
        previous = best.get(key)
        parameters = row.get("parameters") or {}
        rank = (
            abs(int(parameters["subfield_shift"])),
            _factor_height(row),
            int(parameters["subfield_shift"]),
            str(row.get("candidate_hash") or ""),
        )
        if previous is None:
            best[key] = row
            continue
        old_parameters = previous.get("parameters") or {}
        old_rank = (
            abs(int(old_parameters["subfield_shift"])),
            _factor_height(previous),
            int(old_parameters["subfield_shift"]),
            str(previous.get("candidate_hash") or ""),
        )
        if rank < old_rank:
            best[key] = row
    selected = [
        best[key]
        for key in sorted(best, key=lambda value: (-value[0], value[1]))
    ]
    if max_source_seeds is not None:
        selected = selected[: max(0, int(max_source_seeds))]
    return selected


def enumerate_unit_twists(
    base_coefficients: Sequence[int],
    source_rows: Sequence[Mapping[str, Any]],
    *,
    options_per_signature: int = 1,
    gp: str = DEFAULT_GP,
    timeout: float = 3600,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Enumerate compact unit-squareclass twists using one ``bnfinit``."""

    if int(options_per_signature) <= 0:
        raise ValueError("options_per_signature must be positive")
    if not source_rows:
        return [], Counter()
    base = polynomial_expression(base_coefficients, "x")
    expressions = ",".join(
        _fractional_polynomial_expression(row["h_coefficients"], "x")
        for row in source_rows
    )
    script = f"""
x='x; f={base};
if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));
if(polsturm(f)!=12,print("ERROR|base-not-totally-real");quit(1));
b=bnfinit(f);fu=b.fu;H=[{expressions}];
for(si=1,#H,for(mask=0,2^#fu-1,h=Mod(H[si],f);for(j=1,#fu,if(bittest(mask,j-1),h*=fu[j]));hh=lift(h);em=nfeltembed(b,hh);r=2*sum(j=1,12,real(em[j])>0);n=nfeltnorm(b,h);nc=core(numerator(n)*denominator(n));vn=vector(12,j,numerator(polcoef(hh,j-1)));vd=vector(12,j,denominator(polcoef(hh,j-1)));height=vecmax(vector(12,j,max(abs(vn[j]),vd[j])));print("TWIST|",si,"|",mask,"|",r,"|",nc,"|",height,"|",vn,"|",vd)));quit;
"""
    output = _run_gp(script, gp=gp, timeout=timeout)
    if "ERROR|" in output:
        error = next(line for line in output.splitlines() if line.startswith("ERROR|"))
        raise ValueError(error.split("|", 1)[1])
    counts: Counter[str] = Counter()
    by_signature: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for line in output.splitlines():
        if not line.startswith("TWIST|"):
            continue
        parts = line.split("|", 7)
        if len(parts) != 8:
            raise ValueError(f"invalid PARI unit-twist row: {line}")
        _, raw_source, raw_mask, raw_roots, raw_norm, raw_height, raw_num, raw_den = parts
        source_index = int(raw_source) - 1
        if source_index < 0 or source_index >= len(source_rows):
            raise ValueError(f"invalid unit-twist source index {raw_source}")
        numerators = ast.literal_eval(raw_num)
        denominators = ast.literal_eval(raw_den)
        coefficients = tuple(
            Fraction(int(num), int(den))
            for num, den in zip(numerators, denominators)
        )
        while len(coefficients) > 1 and not coefficients[-1]:
            coefficients = coefficients[:-1]
        roots = int(raw_roots)
        norm = int(raw_norm)
        mask = int(raw_mask)
        height = int(raw_height)
        source = source_rows[source_index]
        source_parameters = source.get("parameters") or {}
        for sign, signed_roots, signed_coefficients in (
            (1, roots, coefficients),
            (-1, 24 - roots, tuple(-value for value in coefficients)),
        ):
            # mask=0, sign=+1 is the already-generated raw subfield seed.
            if mask == 0 and sign == 1:
                counts["raw_source_twists_skipped"] += 1
                continue
            # Keep the rational sign branch distinct even when r=12: -1 is
            # a nontrivial invariant squareclass and can toggle the trivial
            # Kummer character without changing the signature.
            by_signature[(source_index, signed_roots, norm, sign)].append({
                "seed_coefficients": signed_coefficients,
                "norm_squareclass": norm,
                "unit_mask": mask,
                "unit_sign": sign,
                "height": height,
                "source_candidate_hash": str(source.get("candidate_hash") or ""),
                "source_subfield_degree": int(source_parameters.get("subfield_degree", 0)),
                "source_subfield_index": int(source_parameters.get("subfield_index", 0)),
                "source_subfield_shift": int(source_parameters.get("subfield_shift", 0)),
                "source_calibrated_target_t": int(
                    source.get("calibrated_target_t") or source.get("verified_target_t") or 0
                ),
            })
            counts["unit_twists_enumerated"] += 1
    selected: list[dict[str, Any]] = []
    for key in sorted(by_signature):
        options = sorted(
            by_signature[key],
            key=lambda row: (
                int(row["height"]),
                int(row["unit_mask"]),
                -int(row["unit_sign"]),
            ),
        )
        selected.extend(options[: int(options_per_signature)])
    counts["unit_twists_selected"] = len(selected)
    counts["unit_twist_signatures"] = len(by_signature)
    return selected, counts


def build_subfield_unit_twist_candidates(
    *,
    source_paths: Sequence[str | Path],
    wreath_map_path: str | Path = DEFAULT_WREATH_MAP,
    options_per_signature: int = 1,
    max_source_seeds_per_field: int | None = None,
    max_lifts_per_field: int | None = 250,
    limit_fields: int | None = None,
    generic_sources: bool = False,
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
            if base_t not in wreath_targets or (wanted_bases and base_t not in wanted_bases):
                continue
            key = (
                base_t,
                tuple(int(value) for value in row["base_coefficients"]),
                str(row.get("source_field_label") or ""),
            )
            grouped[key].append(row)
    counts["source_fields"] = len(grouped)
    unique: dict[str, dict[str, Any]] = {}
    field_groups = sorted(grouped.items())
    if limit_fields is not None:
        field_groups = field_groups[: max(0, int(limit_fields))]
    counts["selected_source_fields"] = len(field_groups)
    for (base_t, base_coefficients, source_label), rows in field_groups:
        sources = select_source_seeds(
            rows,
            max_source_seeds=max_source_seeds_per_field,
            generic_sources=generic_sources,
        )
        counts["selected_source_seeds"] += len(sources)
        if not sources:
            continue
        try:
            twists, twist_counts = enumerate_unit_twists(
                base_coefficients,
                sources,
                options_per_signature=options_per_signature,
                gp=gp,
                timeout=timeout,
            )
            counts.update(twist_counts)
            if max_lifts_per_field is not None:
                twists = twists[: max(0, int(max_lifts_per_field))]
            lifts, lift_counts = build_product_lifts(
                base_coefficients,
                twists,
                gp=gp,
                timeout=timeout,
                coefficient_limit=coefficient_limit,
                absolute_reduction=False,
            )
            counts.update(lift_counts)
        except (RuntimeError, ValueError):
            counts["field_build_failures"] += 1
            continue
        starting_target_t = wreath_targets[base_t]
        source_disc = min(
            (int(row.get("source_field_disc_abs") or 0) for row in rows),
            default=0,
        )
        for lift in lifts:
            line = canonical_coefficients(lift["coefficients"])
            key = candidate_hash(line)
            row = {
                "coefficients": line,
                "candidate_hash": key,
                "local_root_count": int(lift["target_r"]),
                "local_irreducible": True,
                "target_t": int(starting_target_t),
                "target_r": int(lift["target_r"]),
                "label_probability": 0.0,
                "valid_probability": 1.0,
                "recipe_family": (
                    f"subfield_unit_twist_12t{base_t}_d{lift['source_subfield_degree']}_"
                    f"j{lift['source_subfield_index']}_m{lift['unit_mask']}_s{lift['unit_sign']}"
                ),
                "recipe_lineage": f"subfield-unit-twist:12T{base_t}:24T{starting_target_t}",
                "recipe_id": f"subfield-unit-twist:{key[:20]}",
                "construction_overgroup": f"full_C2_wreath_over_12T{base_t}",
                "parameters": {
                    "base_t": base_t,
                    "target_t": int(starting_target_t),
                    "subfield_degree": int(lift["source_subfield_degree"]),
                    "subfield_index": int(lift["source_subfield_index"]),
                    "subfield_shift": int(lift["source_subfield_shift"]),
                    "unit_mask": int(lift["unit_mask"]),
                    "unit_sign": int(lift["unit_sign"]),
                    "norm_squareclass": int(lift["norm_squareclass"]),
                    "source_calibrated_target_t": int(lift["source_calibrated_target_t"]),
                    "overgroup_chain": [int(starting_target_t)],
                },
                "base_t": base_t,
                "base_coefficients": list(base_coefficients),
                "h_coefficients": [_fraction_text(value) for value in lift["seed_coefficients"]],
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
                "source_candidate_hash": str(lift["source_candidate_hash"]),
            }
            previous = unique.get(key)
            if previous is None or int(row["field_disc_abs"]) < int(previous["field_disc_abs"]):
                unique[key] = row
            counts["validity_candidates"] += 1
    output = sorted(
        unique.values(),
        key=lambda row: (
            int(row["base_t"]),
            int(row["target_r"]),
            int(row["field_disc_abs"]),
            str(row["candidate_hash"]),
        ),
    )
    counts["unique_candidates"] = len(output)
    counts["candidate_bases"] = len({int(row["base_t"]) for row in output})
    return output, dict(sorted(counts.items()))


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
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
    parser.add_argument("--options-per-signature", type=int, default=1)
    parser.add_argument("--max-source-seeds-per-field", type=int)
    parser.add_argument("--max-lifts-per-field", type=int, default=250)
    parser.add_argument("--limit-fields", type=int)
    parser.add_argument("--generic-sources", action="store_true")
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--base", action="append", type=int, default=[])
    args = parser.parse_args()
    rows, counts = build_subfield_unit_twist_candidates(
        source_paths=args.source,
        wreath_map_path=args.wreath_map,
        options_per_signature=args.options_per_signature,
        max_source_seeds_per_field=args.max_source_seeds_per_field,
        max_lifts_per_field=args.max_lifts_per_field,
        limit_fields=args.limit_fields,
        generic_sources=args.generic_sources,
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
