#!/usr/bin/env python3
"""Explore the quotient-preserving nested tower stratum above 12T61."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from routeA.build_tower_experiment import random_polynomial
from routeA.constructions.quadratic_tower_cubic import generate_quadratic_tower


C3_BASES = (
    (-1, -3, 0, 1),
    (-1, -4, -1, 1),
    (-1, -5, -2, 1),
)
SCALES = (-3, -2, -1, 1, 2, 3)


def _scale(values: list[int], factor: int) -> list[int]:
    return [factor * value for value in values]


def generate(*, attempts: int, limit: int, seed: int) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    seen: set[str] = set()
    rejected: dict[str, int] = {}
    ratios = [(a, c, d) for a in SCALES for c in SCALES for d in SCALES]
    rng.shuffle(ratios)
    for attempt in range(attempts):
        scale_a, scale_c, scale_d = ratios[attempt % len(ratios)]
        common = random_polynomial(rng, 3)
        parameters = {
            "a1": random_polynomial(rng, 3),
            "a2_A": [0],
            "a2_B": random_polynomial(rng, 3),
            "a3_A": _scale(common, scale_a),
            "a3_B": [0],
            "a3_C": _scale(common, scale_c),
            "a3_D": _scale(common, scale_d),
        }
        try:
            candidate = generate_quadratic_tower(
                C3_BASES[attempt % len(C3_BASES)],
                parameters,
                target_t=0,
                label_probability=0.0,
                cluster_id=(
                    f"q12t61:a{scale_a}:c{scale_c}:d{scale_d}:seed{seed}"
                ),
            )
        except (RuntimeError, ValueError) as exc:
            key = str(exc).split("|")[-1][:80]
            rejected[key] = rejected.get(key, 0) + 1
            continue
        if candidate.candidate_hash in seen:
            continue
        seen.add(candidate.candidate_hash)
        row = candidate.to_json()
        row.update({
            "experiment_seed": seed,
            "experiment_attempt": attempt,
            "q12_target": 61,
            "top_ratio": [scale_a, 0, scale_c, scale_d],
        })
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows, {
        "attempts": attempts,
        "generated": len(rows),
        "ratio_strata": len(ratios),
        "rejected": dict(sorted(rejected.items())),
        "seed": seed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--attempts", type=int, default=1800)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1500081)
    args = parser.parse_args()
    rows, report = generate(attempts=args.attempts, limit=args.limit, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
