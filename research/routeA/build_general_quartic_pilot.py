#!/usr/bin/env python3
"""Build the invariant-stratified GQ-96 portfolio from the DIRAC brief.

This command is deliberately local-only.  It writes candidates and a report;
it has no submission or cloud-compute path.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from routeA.build_empirical_tower_experiment import (
    SEXTIC_6T3_REAL_BASES,
    SEXTIC_6T6_REAL_BASES,
    SEXTIC_6T7_REAL_BASES,
    SEXTIC_6T11_REAL_BASES,
)
from routeA.general_quartic_analyzer import QuarticAnalysisJob, analyze_jobs
from routeA.ledger import candidate_hash, canonical_coefficients


ROOT_TARGETS = (0, 8, 16, 24)
REGIMES = ("S4", "A4", "D4_non_even")
CENTER_FAMILIES = (
    (-12, -3, 4, 11),
    (-14, -5, 6, 13),
    (-15, -4, 7, 12),
    (-17, -6, 8, 15),
)

# Cohort 0 contains one base of every quotient type, making the first 48 a
# balanced calibration batch.  Cohort 1 contains the second base of each type.
SELECTED_BASES = (
    ("6T7", 0, 0, SEXTIC_6T7_REAL_BASES[0]),
    ("6T11", 0, 2, SEXTIC_6T11_REAL_BASES[2]),
    ("6T6", 0, 2, SEXTIC_6T6_REAL_BASES[2]),
    ("6T3", 0, 0, SEXTIC_6T3_REAL_BASES[0]),
    ("6T7", 1, 6, SEXTIC_6T7_REAL_BASES[6]),
    ("6T11", 1, 4, SEXTIC_6T11_REAL_BASES[4]),
    ("6T6", 1, 3, SEXTIC_6T6_REAL_BASES[3]),
    ("6T3", 1, 4, SEXTIC_6T3_REAL_BASES[4]),
)


def real_embeddings(base: Sequence[int]) -> np.ndarray:
    roots = np.roots(list(reversed([int(value) for value in base])))
    if any(abs(value.imag) > 1e-7 for value in roots):
        raise ValueError("GQ-96 requires a totally real sextic base")
    return np.sort(np.real(roots))


def element_expression(coefficients: Sequence[int], variable: str = "t") -> str:
    terms = [
        f"({int(coefficient)})*{variable}^{degree}"
        for degree, coefficient in enumerate(coefficients)
        if int(coefficient)
    ]
    return "(" + "+".join(terms or ["0"]) + ")"


def evaluate_element(coefficients: Sequence[int], embeddings: np.ndarray) -> np.ndarray:
    return sum(
        int(coefficient) * embeddings**degree
        for degree, coefficient in enumerate(coefficients)
    )


def _real_root_count(coefficients: Sequence[float]) -> int:
    return int(sum(bool(abs(root.imag) < 2e-6) for root in np.roots(coefficients)))


def _s4_thresholds(centers: Sequence[int]) -> tuple[float, float]:
    polynomial = np.poly1d(np.poly(centers))
    critical = np.roots(np.polyder(polynomial))
    negative_minima = sorted(
        -float(np.polyval(polynomial, point.real))
        for point in critical
        if abs(point.imag) < 1e-8 and np.polyval(polynomial, point.real) < 0
    )
    if len(negative_minima) != 2:
        raise ValueError("S4 root-control seed must have two negative minima")
    return 0.15 * min(negative_minima), 1.35 * max(negative_minima)


def _find_s4_control(
    embeddings: np.ndarray,
    active_count: int,
    centers: Sequence[int],
    rng: random.Random,
    shift: int,
) -> tuple[list[int], int, np.ndarray]:
    active_cap, inactive_floor = _s4_thresholds(centers)
    if active_count == 0:
        offset = math.ceil(inactive_floor + max((embeddings + shift) ** 2)) + 3
        coefficients = [offset + shift**2, 2 * shift, 1]
        return coefficients, 1, evaluate_element(coefficients, embeddings)
    if active_count == 6:
        coefficients = [1 + shift**2, 2 * shift, 1]
        values = evaluate_element(coefficients, embeddings)
        if max(values) >= active_cap:
            raise ValueError("S4 centers do not leave a safe all-real region")
        return coefficients, 1, values
    for _ in range(80000):
        h = [rng.randint(-5, 5) for _ in range(6)]
        if not any(h):
            continue
        values = evaluate_element(h, embeddings)
        squares = np.sort(values**2)
        small_max = float(squares[active_count - 1])
        large_min = float(squares[active_count])
        lower = inactive_floor / max(large_min, 1e-30)
        upper = active_cap / max(small_max, 1e-30)
        scale = max(1, math.floor(lower) + 1)
        if scale < upper:
            # C = scale*h(t)^2; the GP expression is stored separately.
            return h, scale, scale * values**2
    raise RuntimeError("failed to separate S4 archimedean fibers")


def _a4_root_bounds(t_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    safe, force = [], []
    for value in t_values:
        roots = sorted(np.real(np.roots([1, -value, -(value + 3), -1])))
        safe.append(min(roots[-1], -roots[0]))
        force.append(max(roots[-1], -roots[0]))
    return np.array(safe), np.array(force)


def _find_a4_control(
    embeddings: np.ndarray,
    t_values: np.ndarray,
    active_count: int,
    rng: random.Random,
) -> tuple[list[int], int, np.ndarray]:
    if active_count == 0:
        return [0], 1, np.zeros(6)
    safe, force = _a4_root_bounds(t_values)
    if active_count == 6:
        height = math.ceil(max(force - embeddings)) + 3
        coefficients = [height, 1]
        return coefficients, 1, evaluate_element(coefficients, embeddings)
    best: tuple[tuple[int, int, float], list[int], int, np.ndarray] | None = None
    for _ in range(120000):
        h = [rng.randint(-5, 5) for _ in range(6)]
        if not any(h):
            continue
        values = evaluate_element(h, embeddings)
        order = np.argsort(abs(values))
        inactive = order[: 6 - active_count]
        active = order[6 - active_count :]
        lower = max(abs(values[index]) / safe[index] for index in inactive)
        upper = min(abs(values[index]) / force[index] for index in active)
        denominator = max(1, math.floor(lower) + 1)
        if denominator < upper and denominator <= 64:
            quality = float(upper / max(lower, 1e-12))
            key = (denominator, max(map(abs, h)), -quality)
            if best is None or key < best[0]:
                best = (key, h, denominator, values / denominator)
                if denominator == 1:
                    break
    if best is not None:
        return best[1], best[2], best[3]
    raise RuntimeError("failed to separate A4 archimedean fibers")


def _expanded_coefficients(poly: np.poly1d) -> list[float]:
    return [float(value) for value in poly.coefficients]


def _build_s4(
    embeddings: np.ndarray,
    target_r: int,
    variant: int,
    rng: random.Random,
) -> tuple[str, tuple[str, ...], dict[str, Any]]:
    active_count = target_r // 4
    centers = CENTER_FAMILIES[variant % len(CENTER_FAMILIES)]
    control, scale, control_values = _find_s4_control(
        embeddings, active_count, centers, rng, 1 + variant % 3
    )
    h_expression = element_expression(control)
    if active_count in (0, 6):
        c_expression = h_expression
    else:
        c_expression = f"({scale})*({h_expression})^2"
    quartic = "*".join(f"(X-({center}))" for center in centers) + f"+({c_expression})"
    numeric_roots = 0
    base_poly = np.poly1d(np.poly(centers))
    for c_value in control_values:
        numeric_roots += _real_root_count(
            _expanded_coefficients(base_poly + np.poly1d([c_value]))
        )
    if numeric_roots != target_r:
        raise RuntimeError(f"S4 pre-screen produced r={numeric_roots}, wanted {target_r}")
    return quartic, (), {
        "centers": list(centers),
        "control_element": control,
        "control_scale": scale,
        "numeric_prescreen_r": numeric_roots,
    }


def _build_a4(
    embeddings: np.ndarray,
    target_r: int,
    variant: int,
    rng: random.Random,
) -> tuple[str, tuple[str, ...], dict[str, Any]]:
    active_count = target_r // 4
    t_shift = (variant % 5) - 2
    t_values = embeddings + t_shift
    m_coefficients, denominator, m_values = _find_a4_control(
        embeddings, t_values, active_count, rng
    )
    m_numerator = element_expression(m_coefficients)
    t_numerator = f"({denominator})*(t+({t_shift}))"
    s1 = f"(({t_numerator})+3*({m_numerator}))"
    s2 = (
        f"(-({t_numerator})*({denominator})-3*({denominator})^2+"
        f"2*({m_numerator})*({t_numerator})+3*({m_numerator})^2)"
    )
    s3 = (
        f"(({denominator})^3-({m_numerator})*({t_numerator})*({denominator})"
        f"-3*({m_numerator})*({denominator})^2+({m_numerator})^2*"
        f"({t_numerator})+({m_numerator})^3)"
    )
    quartic = f"X^4-2*({s2})*X^2-8*({s3})*X+(({s2})^2-4*({s1})*({s3}))"
    numeric_roots = 0
    for t_value, m_value in zip(t_values, m_values):
        s1v = t_value + 3 * m_value
        s2v = -(t_value + 3) + 2 * m_value * t_value + 3 * m_value**2
        s3v = (
            1 - m_value * (t_value + 3)
            + m_value**2 * t_value + m_value**3
        )
        # The integral polynomial is Q(y/D); scaling preserves real roots.
        numeric_roots += _real_root_count(
            [1, 0, -2 * s2v, -8 * s3v, s2v**2 - 4 * s1v * s3v]
        )
    if numeric_roots != target_r:
        raise RuntimeError(f"A4 pre-screen produced r={numeric_roots}, wanted {target_r}")
    return quartic, (), {
        "shanks_t_shift": t_shift,
        "m_numerator": m_coefficients,
        "denominator": denominator,
        "numeric_prescreen_r": numeric_roots,
    }


def _build_d4(
    embeddings: np.ndarray,
    target_r: int,
    variant: int,
    rng: random.Random,
) -> tuple[str, tuple[str, ...], dict[str, Any]]:
    active_count = target_r // 4
    if active_count == 0:
        shift = 1 + variant % 3
        t_expression = f"-({2 + variant % 3})-(t+({shift}))^2"
        t_values = -(2 + variant % 3) - (embeddings + shift) ** 2
    elif active_count == 6:
        shift = 1 + variant % 3
        t_expression = f"({2 + variant % 3})+(t+({shift}))^2"
        t_values = (2 + variant % 3) + (embeddings + shift) ** 2
    else:
        gap = (embeddings[active_count - 1] + embeddings[active_count]) / 2
        denominator = 5 + (variant % 11)
        numerator = None
        while denominator < 1000:
            low = denominator * embeddings[active_count - 1]
            high = denominator * embeddings[active_count]
            candidate = math.floor(high - 1e-10)
            if candidate > low + 1e-10:
                numerator = candidate
                break
            denominator += 1
        if numerator is None:
            raise RuntimeError("failed to place a D4 sign-control threshold")
        raw = numerator - denominator * embeddings
        scale = max(1, math.ceil(2.0 / min(raw[:active_count])))
        t_expression = f"({scale})*(({numerator})-({denominator})*t)"
        t_values = scale * raw
    translation = (variant % 5) - 2
    u_expression = f"(t+({translation}))"
    positive_scale = (1, 2, 3, 5)[variant % 4]
    quartic = (
        f"(X-({u_expression}))^4-2*({positive_scale})*({t_expression})*"
        f"(X-({u_expression}))^2+({positive_scale})^2*({t_expression})*"
        f"(({t_expression})-1)"
    )
    numeric_roots = 0
    for embedding, t_value in zip(embeddings, t_values):
        u = embedding + translation
        z = np.poly1d([1, -u])
        poly = z**4 - 2 * positive_scale * t_value * z**2 \
            + positive_scale**2 * t_value * (t_value - 1)
        numeric_roots += _real_root_count(_expanded_coefficients(poly))
    if numeric_roots != target_r:
        raise RuntimeError(f"D4 pre-screen produced r={numeric_roots}, wanted {target_r}")
    return quartic, (t_expression, f"({t_expression})-1"), {
        "t_class": t_expression,
        "translation": u_expression,
        "positive_scale": positive_scale,
        "numeric_prescreen_r": numeric_roots,
    }


def make_job(
    *,
    index: int,
    base_group: str,
    cohort: int,
    source_index: int,
    base: Sequence[int],
    regime: str,
    target_r: int,
    variant: int,
    seed: int,
) -> QuarticAnalysisJob:
    embeddings = real_embeddings(base)
    rng = random.Random(seed + index * 1000003 + variant * 9176)
    if regime == "S4":
        quartic, classes, parameters = _build_s4(embeddings, target_r, variant, rng)
    elif regime == "A4":
        quartic, classes, parameters = _build_a4(embeddings, target_r, variant, rng)
    elif regime == "D4_non_even":
        quartic, classes, parameters = _build_d4(embeddings, target_r, variant, rng)
    else:
        raise ValueError(f"unknown regime {regime}")
    return QuarticAnalysisJob(
        index=index,
        base_coefficients=tuple(map(int, base)),
        quartic_expression=quartic,
        requested_regime=regime,
        requested_root_count=target_r,
        squareclass_expressions=classes,
        metadata={
            "base_group": base_group,
            "base_cohort": cohort,
            "base_source_index": source_index,
            "regime": regime,
            "target_r": target_r,
            "variant": variant,
            **parameters,
        },
    )


def all_cells(base_limit: int = 8) -> list[tuple[Any, ...]]:
    return [
        (base_group, cohort, source_index, base, regime, target_r)
        for base_group, cohort, source_index, base in SELECTED_BASES[:base_limit]
        for regime in REGIMES
        for target_r in ROOT_TARGETS
    ]


def _fingerprint_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    a = {(prime, tuple(parts)) for prime, parts in left.items()}
    b = {(prime, tuple(parts)) for prime, parts in right.items()}
    return 1.0 if not a and not b else 1 - len(a & b) / max(1, len(a | b))


def select_portfolio(
    candidates: Iterable[dict[str, Any]], cells: Sequence[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    by_cell: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        metadata = candidate["job_metadata"]
        by_cell[(metadata["base_group"], metadata["base_source_index"],
                 metadata["regime"], metadata["target_r"])].append(candidate)
    selected: list[dict[str, Any]] = []
    fingerprints: list[dict[str, Any]] = []
    for base_group, _, source_index, _, regime, target_r in cells:
        options = by_cell.get((base_group, source_index, regime, target_r), [])
        if not options:
            continue
        def score(option: dict[str, Any]) -> tuple[float, float]:
            fingerprint = option["frobenius_cycle_types"]
            novelty = min(
                (_fingerprint_distance(fingerprint, previous)
                 for previous in fingerprints),
                default=1.0,
            )
            rank_quality = min(option["squareclass_rank"], 2) / 2
            size_penalty = math.log10(max(10, option["coefficient_height"])) / 55
            return 0.55 * novelty + 0.30 * rank_quality - 0.15 * size_penalty, -size_penalty
        choice = max(options, key=score)
        choice["novelty_score"] = score(choice)[0]
        selected.append(choice)
        fingerprints.append(choice["frobenius_cycle_types"])
    return selected


def generate_portfolio(
    *,
    pool_per_cell: int,
    attempts_per_cell: int,
    seed: int,
    base_limit: int = 8,
    chunk_size: int = 192,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cells = all_cells(base_limit)
    jobs: list[QuarticAnalysisJob] = []
    index = 0
    construction_errors: Counter[str] = Counter()
    for cell in cells:
        base_group, cohort, source_index, base, regime, target_r = cell
        for variant in range(attempts_per_cell):
            try:
                jobs.append(make_job(
                    index=index,
                    base_group=base_group,
                    cohort=cohort,
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
    analyzed: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    cell_counts: Counter[tuple[str, int, str, int]] = Counter()
    for start in range(0, len(jobs), chunk_size):
        valid, rejected = analyze_jobs(jobs[start : start + chunk_size])
        rejection_counts.update(rejected)
        for row in valid:
            metadata = row["job_metadata"]
            key = (metadata["base_group"], metadata["base_source_index"],
                   metadata["regime"], metadata["target_r"])
            if cell_counts[key] >= pool_per_cell:
                continue
            cell_counts[key] += 1
            serialized = canonical_coefficients(row["coefficients"])
            row["candidate_hash"] = candidate_hash(serialized)
            row["coefficients_serialized"] = serialized
            analyzed.append(row)
    selected = select_portfolio(analyzed, cells)
    output: list[dict[str, Any]] = []
    for rank, row in enumerate(selected):
        metadata = row["job_metadata"]
        output.append({
            "candidate_id": row["candidate_hash"],
            "candidate_hash": row["candidate_hash"],
            "architecture": "GQ-96-controlled-general-quartic",
            "base_group": metadata["base_group"],
            "base_polynomial": next(
                list(base) for group, _, source, base in SELECTED_BASES
                if group == metadata["base_group"]
                and source == metadata["base_source_index"]
            ),
            "relative_polynomial": next(
                job.quartic_expression for job in jobs
                if job.metadata == metadata
            ),
            "relative_group_prediction": row["relative_group_prediction"],
            "resultant_polynomial": row["coefficients"],
            "coefficients": row["coefficients_serialized"],
            "degree": 24,
            "monic": True,
            "irreducible": True,
            "local_irreducible": True,
            "real_root_count": row["real_root_count"],
            "local_root_count": row["real_root_count"],
            "target_r": metadata["target_r"],
            "target_t": 0,
            "discriminant": str(row["global_polynomial_discriminant"]),
            "coefficient_height": str(row["coefficient_height"]),
            "quartic_discriminant": row["quartic_discriminant_coefficients"],
            "quartic_discriminant_norm": str(row["quartic_discriminant_norm"]),
            "quartic_discriminant_square": row["quartic_discriminant_square"],
            "cubic_resolvent": (
                "z^3-b*z^2+(a*c-4*d)*z+(4*b*d-a^2*d-c^2)"
            ),
            "cubic_resolvent_factorization": list(row["resolvent_factor_degrees"]),
            "squareclass_rank": row["squareclass_rank"],
            "squareclass_matrix": row["squareclass_matrix"],
            "intersection_fingerprint": row["intersection_fingerprint"],
            "frobenius_cycle_types": row["frobenius_cycle_types"],
            "predicted_24T_labels": [],
            "structural_stratum": (
                f"{metadata['base_group']}:{metadata['regime']}:r{metadata['target_r']}"
            ),
            "novelty_score": row["novelty_score"],
            "submission_priority": len(selected) - rank,
            "parameters": metadata,
            "recipe_family": "GQ96",
            "source_host": "local-pari-exact",
            "submitted": False,
            "server_accepted": None,
            "server_scoreable": None,
            "server_24T": None,
            "server_r": None,
            "server_points": None,
        })
    first_batch = [row for row in output if row["parameters"]["base_cohort"] == 0]
    report = {
        "architecture": "GQ-96",
        "requested_cells": len(cells),
        "selected": len(output),
        "batch_1_size": len(first_batch),
        "batch_2_size": len(output) - len(first_batch),
        "pool_per_cell": pool_per_cell,
        "attempts_per_cell": attempts_per_cell,
        "pari_jobs": len(jobs),
        "locally_valid_pool": len(analyzed),
        "construction_errors": dict(construction_errors),
        "pari_rejections": dict(rejection_counts),
        "missing_cells": len(cells) - len(output),
        "exact_checks": [
            "relative irreducibility",
            "cubic resolvent factorization",
            "number-field discriminant square test",
            "D4 marker-prime squareclass rank",
            "degree-24 rational irreducibility",
            "exact Sturm real-root count",
            "30-prime Frobenius fingerprint",
        ],
        "certification_limits": [
            "GAP degree-24 group IDs are unavailable locally",
            "S4/A4 normal-closure intersection is a ramification screen, not a proof",
            "S4 conjugate discriminant-orbit rank is not yet certified in the normal closure",
        ],
        "automatic_submission_authorized": False,
        "submission_ready": False,
        "submission_note": "Local candidate construction only; no server or GCP action taken.",
    }
    return output, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--batch1", type=Path)
    parser.add_argument("--pool-per-cell", type=int, default=100)
    parser.add_argument("--attempts-per-cell", type=int, default=140)
    parser.add_argument("--seed", type=int, default=240096)
    parser.add_argument("--base-limit", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--chunk-size", type=int, default=192)
    args = parser.parse_args()
    rows, report = generate_portfolio(
        pool_per_cell=args.pool_per_cell,
        attempts_per_cell=args.attempts_per_cell,
        seed=args.seed,
        base_limit=args.base_limit,
        chunk_size=args.chunk_size,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    if args.batch1:
        batch1 = [row for row in rows if row["parameters"]["base_cohort"] == 0]
        args.batch1.parent.mkdir(parents=True, exist_ok=True)
        args.batch1.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in batch1),
            encoding="utf-8",
        )
    report_path = args.report or args.output.with_suffix(".report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
