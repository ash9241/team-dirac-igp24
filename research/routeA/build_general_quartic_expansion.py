#!/usr/bin/env python3
"""Expand GQ-96 across the unused totally-real sextic source fields.

The original GQ-96 portfolio deliberately used two calibration sources from
each of four sextic quotient groups.  Once both 48-row cohorts have passed
server validation, the remaining totally-real sources are independent,
locally certifiable exploration cells.  This command constructs one candidate
per ``(source field, quartic regime, real-root target)`` without submitting it.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from routeA.build_empirical_tower_experiment import (
    SEXTIC_6T3_REAL_BASES,
    SEXTIC_6T6_REAL_BASES,
    SEXTIC_6T7_REAL_BASES,
    SEXTIC_6T11_REAL_BASES,
)
from routeA.build_general_quartic_pilot import (
    REGIMES,
    ROOT_TARGETS,
    SELECTED_BASES,
    make_job,
)
from routeA.general_quartic_analyzer import QuarticAnalysisJob, analyze_jobs
from routeA.ledger import candidate_hash, canonical_coefficients


BASES: dict[str, Sequence[Sequence[int]]] = {
    "6T3": SEXTIC_6T3_REAL_BASES,
    "6T6": SEXTIC_6T6_REAL_BASES,
    "6T7": SEXTIC_6T7_REAL_BASES,
    "6T11": SEXTIC_6T11_REAL_BASES,
}


def expansion_bases() -> list[tuple[str, int, tuple[int, ...]]]:
    used = {(str(group), int(index)) for group, _, index, _ in SELECTED_BASES}
    return [
        (group, index, tuple(map(int, base)))
        for group, bases in BASES.items()
        for index, base in enumerate(bases)
        if (group, index) not in used
    ]


def _cell(job: QuarticAnalysisJob) -> tuple[str, int, str, int]:
    metadata = job.metadata
    return (
        str(metadata["base_group"]),
        int(metadata["base_source_index"]),
        str(metadata["regime"]),
        int(metadata["target_r"]),
    )


def generate_expansion(
    *,
    seed: int = 240197,
    attempts_per_cell: int = 2,
    chunk_size: int = 192,
    bases: Sequence[tuple[str, int, Sequence[int]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if attempts_per_cell < 1:
        raise ValueError("attempts_per_cell must be positive")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    selected_bases = [
        (str(group), int(source_index), tuple(map(int, base)))
        for group, source_index, base in (bases or expansion_bases())
    ]
    if len({(group, index) for group, index, _ in selected_bases}) != len(selected_bases):
        raise ValueError("base group/source indexes must be unique")
    jobs: list[QuarticAnalysisJob] = []
    construction_errors: Counter[str] = Counter()
    index = 0
    for group, source_index, base in selected_bases:
        for regime in REGIMES:
            for target_r in ROOT_TARGETS:
                for variant in range(attempts_per_cell):
                    try:
                        jobs.append(make_job(
                            index=index,
                            base_group=group,
                            cohort=2,
                            source_index=source_index,
                            base=base,
                            regime=regime,
                            target_r=target_r,
                            variant=variant,
                            seed=seed,
                        ))
                        index += 1
                    except (RuntimeError, ValueError) as error:
                        construction_errors[type(error).__name__ + ":" + str(error)] += 1

    job_by_metadata = {
        (
            str(job.metadata["base_group"]),
            int(job.metadata["base_source_index"]),
            str(job.metadata["regime"]),
            int(job.metadata["target_r"]),
            int(job.metadata["variant"]),
        ): job
        for job in jobs
    }
    valid: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for start in range(0, len(jobs), chunk_size):
        rows, counts = analyze_jobs(jobs[start : start + chunk_size])
        valid.extend(rows)
        rejected.update(counts)

    # Prefer the first valid variant for each structural/root cell.  Distinct
    # source fields remain separate because they can realize sibling 24T labels.
    valid.sort(key=lambda row: (
        str(row["job_metadata"]["base_group"]),
        int(row["job_metadata"]["base_source_index"]),
        str(row["job_metadata"]["regime"]),
        int(row["job_metadata"]["target_r"]),
        int(row["job_metadata"]["variant"]),
        int(row["coefficient_height"]),
    ))
    chosen: dict[tuple[str, int, str, int], dict[str, Any]] = {}
    for row in valid:
        metadata = row["job_metadata"]
        key = (
            str(metadata["base_group"]),
            int(metadata["base_source_index"]),
            str(metadata["regime"]),
            int(metadata["target_r"]),
        )
        chosen.setdefault(key, row)

    output: list[dict[str, Any]] = []
    for key, row in sorted(chosen.items()):
        metadata = dict(row["job_metadata"])
        job_key = (*key, int(metadata["variant"]))
        job = job_by_metadata[job_key]
        coefficients = canonical_coefficients(row["coefficients"])
        output.append({
            "candidate_id": candidate_hash(coefficients),
            "candidate_hash": candidate_hash(coefficients),
            "architecture": "GQ-expanded-controlled-general-quartic",
            "base_group": metadata["base_group"],
            "base_polynomial": list(map(int, job.base_coefficients)),
            "relative_polynomial": job.quartic_expression,
            "relative_group_prediction": row["relative_group_prediction"],
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
            "quartic_discriminant_square": bool(row["quartic_discriminant_square"]),
            "squareclass_rank": int(row["squareclass_rank"]),
            "intersection_fingerprint": row["intersection_fingerprint"],
            "frobenius_cycle_types": row["frobenius_cycle_types"],
            "parameters": metadata,
            "recipe_family": "GQ-expanded",
            "source_host": "local-pari-exact",
            "submitted": False,
        })

    total_cells = len(selected_bases) * len(REGIMES) * len(ROOT_TARGETS)
    report = {
        "architecture": "GQ-expanded",
        "source_fields": len(selected_bases),
        "requested_cells": total_cells,
        "attempts_per_cell": int(attempts_per_cell),
        "pari_jobs": len(jobs),
        "locally_valid_attempts": len(valid),
        "selected": len(output),
        "missing_cells": total_cells - len(output),
        "construction_errors": dict(sorted(construction_errors.items())),
        "pari_rejections": dict(sorted(rejected.items())),
        "submission_ready": False,
        "automatic_submission_authorized": False,
    }
    return output, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--seed", type=int, default=240197)
    parser.add_argument("--attempts-per-cell", type=int, default=2)
    parser.add_argument("--chunk-size", type=int, default=192)
    parser.add_argument(
        "--catalog",
        type=Path,
        help="JSONL degree-6 sources; overrides the built-in unused-base list",
    )
    args = parser.parse_args()
    bases = None
    if args.catalog is not None:
        rows = [
            json.loads(line)
            for line in args.catalog.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        bases = [
            (
                str(row["base_group"]),
                int(row.get("source_index", index)),
                tuple(map(int, row["coefficients"])),
            )
            for index, row in enumerate(rows)
        ]
    rows, report = generate_expansion(
        seed=args.seed,
        attempts_per_cell=args.attempts_per_cell,
        chunk_size=args.chunk_size,
        bases=bases,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_path = args.report or args.output.with_suffix(".report.json")
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
