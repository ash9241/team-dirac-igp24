#!/usr/bin/env python3
"""Explore dependency masks in genuine cubic -> 2 -> 4 -> 8 towers.

The generic tower sampler makes every coefficient nonzero.  That overwhelmingly
lands in the maximal wreath groups.  This sampler deliberately varies which
subfield basis terms occur in the second and third quadratic radicands, and
also varies linear relations among the active coefficients.  Those degeneracy
strata are the arithmetic mechanism that produces the many proper subgroups in
the degree-24 2/4/8 block census.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from routeA.build_tower_experiment import DEFAULT_BASES, random_polynomial
from routeA.constructions.quadratic_tower_cubic import generate_quadratic_tower


EXTRA_BASES = (
    (-1, 1, 0, 1),       # S3, one real root
    (2, -2, 0, 1),       # S3, one real root
    (1, -4, 0, 1),       # S3, three real roots
    (-2, -2, 0, 1),      # S3
    (1, -5, 0, 1),       # S3, three real roots
)
BASES = DEFAULT_BASES + EXTRA_BASES
ZERO = [0]


def _scale(values: list[int], factor: int) -> list[int]:
    return [factor * value for value in values]


def _active_values(
    rng: random.Random,
    count: int,
    bound: int,
    mode: str,
) -> list[list[int]]:
    if mode == "independent":
        return [random_polynomial(rng, bound) for _ in range(count)]
    if mode == "tied":
        common = random_polynomial(rng, bound)
        return [_scale(common, rng.choice((-2, -1, 1, 2))) for _ in range(count)]
    if mode == "constant-mix":
        return [
            [rng.choice((-3, -2, -1, 1, 2, 3))]
            if rng.random() < 0.65
            else random_polynomial(rng, bound)
            for _ in range(count)
        ]
    raise ValueError(f"unsupported coefficient mode: {mode}")


def _masked_layer(
    rng: random.Random,
    mask: int,
    width: int,
    bound: int,
    mode: str,
) -> list[list[int]]:
    positions = [index for index in range(width) if mask & (1 << index)]
    active = iter(_active_values(rng, len(positions), bound, mode))
    return [next(active) if index in positions else ZERO[:] for index in range(width)]


def generate_masked_towers(
    *,
    attempts: int,
    limit: int,
    seed: int,
    coefficient_bound: int = 3,
) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    seen: set[str] = set()
    modes = ("independent", "tied", "constant-mix")
    # Shuffle a complete structural grid and cycle it.  Every 135 attempts
    # covers the three degree-2 masks, all 15 nonempty degree-4 masks, and all
    # three coefficient-relation modes.
    strata = [
        (a2_mask, a3_mask, mode)
        for a2_mask in (1, 2, 3)
        for a3_mask in range(1, 16)
        for mode in modes
    ]
    rng.shuffle(strata)
    rejected: dict[str, int] = {}
    for attempt in range(attempts):
        a2_mask, a3_mask, mode = strata[attempt % len(strata)]
        cubic = BASES[(attempt // len(strata) + rng.randrange(len(BASES))) % len(BASES)]
        a2_a, a2_b = _masked_layer(
            rng, a2_mask, 2, coefficient_bound, mode
        )
        a3_a, a3_b, a3_c, a3_d = _masked_layer(
            rng, a3_mask, 4, coefficient_bound, mode
        )
        parameters = {
            "a1": random_polynomial(rng, coefficient_bound),
            "a2_A": a2_a,
            "a2_B": a2_b,
            "a3_A": a3_a,
            "a3_B": a3_b,
            "a3_C": a3_c,
            "a3_D": a3_d,
        }
        try:
            candidate = generate_quadratic_tower(
                cubic,
                parameters,
                target_t=0,
                label_probability=0.0,
                cluster_id=(
                    f"masked:a2-{a2_mask}:a3-{a3_mask}:mode-{mode}:seed-{seed}"
                ),
                second_primitive_base_shift=1,
                second_primitive_u_shift=1,
                final_primitive_base_shift=1,
                final_primitive_u_shift=1,
                final_primitive_v_shift=1,
            )
        except (ValueError, RuntimeError) as exc:
            reason = str(exc).split("|")[-1][:80]
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        if candidate.candidate_hash in seen:
            continue
        seen.add(candidate.candidate_hash)
        row = candidate.to_json()
        row.update({
            "experiment_seed": seed,
            "experiment_attempt": attempt,
            "dependency_masks": {"a2": a2_mask, "a3": a3_mask},
            "coefficient_relation_mode": mode,
        })
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows, {
        "attempts_requested": attempts,
        "generated": len(rows),
        "seed": seed,
        "strata": len(strata),
        "rejected": dict(sorted(rejected.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--attempts", type=int, default=5000)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1500021)
    parser.add_argument("--coefficient-bound", type=int, default=3)
    args = parser.parse_args()
    rows, report = generate_masked_towers(
        attempts=args.attempts,
        limit=args.limit,
        seed=args.seed,
        coefficient_bound=args.coefficient_bound,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
