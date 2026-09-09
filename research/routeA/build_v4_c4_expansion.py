#!/usr/bin/env python3
"""Build controlled relative V4 and cyclic-C4 quartics over all sextic groups."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from routeA.build_general_quartic_expansion import BASES
from routeA.build_general_quartic_pilot import element_expression, real_embeddings
from routeA.general_quartic_analyzer import QuarticAnalysisJob, analyze_jobs
from routeA.ledger import candidate_hash, canonical_coefficients


ROOT_TARGETS = tuple(range(0, 25, 4))
REGIMES = ("V4", "C4")


def all_sextic_bases(catalogs: Sequence[Path]) -> list[tuple[str, int, tuple[int, ...]]]:
    selected: dict[str, tuple[str, int, tuple[int, ...]]] = {
        group: (group, 0, tuple(map(int, bases[0])))
        for group, bases in BASES.items()
    }
    for catalog in catalogs:
        for line in catalog.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            group = str(row["base_group"])
            selected[group] = (
                group,
                int(row.get("source_index", 0)),
                tuple(map(int, row["coefficients"])),
            )
    rows = sorted(selected.values(), key=lambda item: int(item[0][2:]))
    if [int(group[2:]) for group, _, _ in rows] != list(range(1, 17)):
        raise ValueError("catalogs must cover every sextic transitive group 6T1..6T16")
    return rows


def sign_control(
    embeddings: np.ndarray, active_count: int, variant: int
) -> tuple[str, np.ndarray, dict[str, Any]]:
    if active_count == 0:
        shift = 1 + variant % 4
        values = -(3 + (embeddings + shift) ** 2)
        return (
            f"-(3+(t+({shift}))^2)",
            values,
            {"kind": "negative-square", "shift": shift},
        )
    if active_count == 6:
        shift = 1 + variant % 4
        values = 3 + (embeddings + shift) ** 2
        return (
            f"(3+(t+({shift}))^2)",
            values,
            {"kind": "positive-square", "shift": shift},
        )
    denominator = 5 + variant
    numerator = None
    while denominator < 5000:
        low = denominator * embeddings[active_count - 1]
        high = denominator * embeddings[active_count]
        candidate = math.floor(high - 1e-9)
        if candidate > low + 1e-9:
            numerator = candidate
            break
        denominator += 1
    if numerator is None:
        raise RuntimeError("failed to place a sextic sign threshold")
    values = numerator - denominator * embeddings
    if sum(bool(value > 1e-8) for value in values) != active_count:
        raise RuntimeError("sign threshold missed requested active embeddings")
    return (
        f"(({numerator})-({denominator})*t)",
        values,
        {"kind": "linear-threshold", "numerator": numerator, "denominator": denominator},
    )


def make_job(
    *,
    index: int,
    group: str,
    source_index: int,
    base: Sequence[int],
    regime: str,
    target_r: int,
    variant: int,
) -> QuarticAnalysisJob:
    embeddings = real_embeddings(base)
    active_count = int(target_r) // 4
    control, values, parameters = sign_control(embeddings, active_count, variant)
    if regime == "V4":
        # Roots are +/-sqrt(2) +/- sqrt(control).  There are four real roots
        # exactly at the embeddings where ``control`` is positive.
        quartic = f"X^4-2*(2+({control}))*X^2+(2-({control}))^2"
        classes = ("2", control)
        extra: dict[str, Any] = {"v4_squareclasses": ["2", control]}
    elif regime == "C4":
        translation = (variant % 5) - 2
        b_value = 1 + (variant % 3)
        a_expression = f"(t+({translation}))"
        d_expression = f"(({a_expression})^2+({b_value})^2)"
        quartic = (
            f"X^4-2*({control})*({d_expression})*X^2+"
            f"({control})^2*({d_expression})*({b_value})^2"
        )
        classes = (d_expression, control)
        extra = {
            "A": a_expression,
            "B": b_value,
            "D": d_expression,
            "cyclic_quartic_certificate": "D=A^2+B^2",
        }
    else:
        raise ValueError(f"unsupported regime: {regime}")
    numeric_r = 4 * sum(bool(value > 0) for value in values)
    if numeric_r != int(target_r):
        raise RuntimeError(f"numeric sign control produced r={numeric_r}, wanted {target_r}")
    metadata = {
        "base_group": group,
        "base_source_index": int(source_index),
        "regime": regime,
        "target_r": int(target_r),
        "variant": int(variant),
        "numeric_prescreen_r": numeric_r,
        "sign_control": parameters,
        **extra,
    }
    return QuarticAnalysisJob(
        index=int(index),
        base_coefficients=tuple(map(int, base)),
        quartic_expression=quartic,
        requested_regime=regime,
        requested_root_count=int(target_r),
        squareclass_expressions=tuple(classes),
        metadata=metadata,
    )


def generate(
    bases: Sequence[tuple[str, int, Sequence[int]]],
    *,
    attempts_per_cell: int = 4,
    chunk_size: int = 96,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    jobs: list[QuarticAnalysisJob] = []
    construction_errors: Counter[str] = Counter()
    for group, source_index, base in bases:
        for regime in REGIMES:
            for target_r in ROOT_TARGETS:
                for variant in range(attempts_per_cell):
                    try:
                        jobs.append(make_job(
                            index=len(jobs),
                            group=group,
                            source_index=source_index,
                            base=base,
                            regime=regime,
                            target_r=target_r,
                            variant=variant,
                        ))
                    except (RuntimeError, ValueError) as error:
                        construction_errors[f"{type(error).__name__}:{error}"] += 1
    valid: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for start in range(0, len(jobs), chunk_size):
        rows, counts = analyze_jobs(jobs[start : start + chunk_size])
        valid.extend(rows)
        rejected.update(counts)
    valid.sort(key=lambda row: (
        int(str(row["job_metadata"]["base_group"])[2:]),
        str(row["job_metadata"]["regime"]),
        int(row["job_metadata"]["target_r"]),
        int(row["coefficient_height"]),
        int(row["job_metadata"]["variant"]),
    ))
    chosen: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in valid:
        metadata = row["job_metadata"]
        key = (
            str(metadata["base_group"]),
            str(metadata["regime"]),
            int(metadata["target_r"]),
        )
        chosen.setdefault(key, row)
    output: list[dict[str, Any]] = []
    for row in chosen.values():
        coefficients = canonical_coefficients(row["coefficients"])
        metadata = dict(row["job_metadata"])
        output.append({
            "candidate_id": candidate_hash(coefficients),
            "candidate_hash": candidate_hash(coefficients),
            "architecture": "controlled-relative-v4-c4-over-sextic",
            "base_group": metadata["base_group"],
            "relative_group_prediction": metadata["regime"],
            "coefficients": coefficients,
            "degree": 24,
            "monic": True,
            "irreducible": True,
            "local_irreducible": True,
            "local_root_count": int(row["real_root_count"]),
            "target_r": int(row["real_root_count"]),
            "target_t": 0,
            "discriminant": str(row["global_polynomial_discriminant"]),
            "coefficient_height": str(row["coefficient_height"]),
            "parameters": metadata,
            "recipe_family": "V4-C4-expanded",
            "source_host": "local-pari-exact",
            "submitted": False,
        })
    requested_cells = len(bases) * len(REGIMES) * len(ROOT_TARGETS)
    report = {
        "architecture": "V4-C4-expanded",
        "source_fields": len(bases),
        "requested_cells": requested_cells,
        "attempts_per_cell": attempts_per_cell,
        "pari_jobs": len(jobs),
        "locally_valid_attempts": len(valid),
        "selected": len(output),
        "missing_cells": requested_cells - len(output),
        "construction_errors": dict(sorted(construction_errors.items())),
        "pari_rejections": dict(sorted(rejected.items())),
    }
    return output, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--catalog", action="append", type=Path, default=[])
    parser.add_argument("--attempts-per-cell", type=int, default=4)
    parser.add_argument("--chunk-size", type=int, default=96)
    args = parser.parse_args()
    bases = all_sextic_bases(args.catalog)
    rows, report = generate(
        bases,
        attempts_per_cell=args.attempts_per_cell,
        chunk_size=args.chunk_size,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_path = args.output.with_suffix(".report.json")
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
