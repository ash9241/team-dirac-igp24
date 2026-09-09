#!/usr/bin/env python3
"""Generate a reproducible offline sample of genuine 3x2x2x2 field towers."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from routeA.constructions.quadratic_tower_cubic import generate_quadratic_tower


DEFAULT_BASES = (
    (-1, -1, 0, 1),       # 3T2: x^3-x-1
    (-2, 0, 0, 1),        # 3T2: x^3-2
    (-1, -3, 0, 1),       # 3T1 Shanks cubic, t=0
    (-1, -4, -1, 1),      # 3T1 Shanks cubic, t=1
    (-1, -5, -2, 1),      # 3T1 Shanks cubic, t=2
)


def random_polynomial(rng: random.Random, bound: int) -> list[int]:
    values = [rng.randint(-bound, bound) for _ in range(3)]
    if not any(values):
        values[rng.randrange(3)] = 1
    while len(values) > 1 and values[-1] == 0:
        values.pop()
    return values


def _trim(values: list[int]) -> list[int]:
    while len(values) > 1 and values[-1] == 0:
        values.pop()
    return values


def _add(*values: list[int]) -> list[int]:
    result = [0] * max(map(len, values))
    for value in values:
        for index, coefficient in enumerate(value):
            result[index] += coefficient
    return _trim(result)


def _scale(value: list[int], factor: int) -> list[int]:
    return _trim([factor * coefficient for coefficient in value])


def _multiply_mod(
    left: list[int], right: list[int], modulus: tuple[int, ...]
) -> list[int]:
    """Multiply in Z[x]/(modulus), where modulus is monic cubic."""

    product = [0] * (len(left) + len(right) - 1)
    for left_index, left_coefficient in enumerate(left):
        for right_index, right_coefficient in enumerate(right):
            product[left_index + right_index] += left_coefficient * right_coefficient
    for degree in range(len(product) - 1, 2, -1):
        leading = product[degree]
        if not leading:
            continue
        product[degree] = 0
        for index in range(3):
            product[degree - 3 + index] -= leading * int(modulus[index])
    return _trim(product[:3])


def positive_tower_parameters(
    rng: random.Random,
    cubic: tuple[int, ...],
    bound: int,
) -> dict[str, list[int]]:
    """Return three independent square-plus-positive tower radicands.

    At every real embedding each new radicand is ``1 + element^2``.  Over a
    totally real cubic this forces all 24 embeddings to remain real while the
    element-dependent squareclasses still explore nontrivial tower groups.
    """

    h1, a, b, c, d = (random_polynomial(rng, bound) for _ in range(5))
    one = [rng.randint(1, 3)]
    a1 = _add(one, _multiply_mod(h1, h1, cubic))

    a2_a = _add(
        [rng.randint(1, 3)],
        _multiply_mod(a, a, cubic),
        _multiply_mod(_multiply_mod(b, b, cubic), a1, cubic),
    )
    a2_b = _scale(_multiply_mod(a, b, cubic), 2)

    p2_constant = _add(
        _multiply_mod(a, a, cubic),
        _multiply_mod(_multiply_mod(b, b, cubic), a1, cubic),
    )
    p2_u = _scale(_multiply_mod(a, b, cubic), 2)
    q2_constant = _add(
        _multiply_mod(c, c, cubic),
        _multiply_mod(_multiply_mod(d, d, cubic), a1, cubic),
    )
    q2_u = _scale(_multiply_mod(c, d, cubic), 2)
    q2_v2_constant = _add(
        _multiply_mod(q2_constant, a2_a, cubic),
        _multiply_mod(
            _multiply_mod(q2_u, a2_b, cubic), a1, cubic
        ),
    )
    q2_v2_u = _add(
        _multiply_mod(q2_constant, a2_b, cubic),
        _multiply_mod(q2_u, a2_a, cubic),
    )
    pq_constant = _add(
        _multiply_mod(a, c, cubic),
        _multiply_mod(_multiply_mod(b, d, cubic), a1, cubic),
    )
    pq_u = _add(
        _multiply_mod(a, d, cubic),
        _multiply_mod(b, c, cubic),
    )
    return {
        "a1": a1,
        "a2_A": a2_a,
        "a2_B": a2_b,
        "a3_A": _add([rng.randint(1, 3)], p2_constant, q2_v2_constant),
        "a3_B": _add(p2_u, q2_v2_u),
        "a3_C": _scale(pq_constant, 2),
        "a3_D": _scale(pq_u, 2),
    }


def generate_experiment(
    *,
    attempts: int,
    limit: int,
    seed: int,
    coefficient_bound: int = 2,
    positive_towers: bool = False,
) -> list[dict]:
    rng = random.Random(seed)
    records = []
    seen = set()
    names = ("a1", "a2_A", "a2_B", "a3_A", "a3_B", "a3_C", "a3_D")
    for attempt in range(attempts):
        # The final three Shanks cubics are totally real.  Combined with
        # square-plus-positive radicands they give a deliberate r=24 lane.
        cubic_index = rng.randrange(2, len(DEFAULT_BASES)) if positive_towers else rng.randrange(len(DEFAULT_BASES))
        parameters = (
            positive_tower_parameters(
                rng, DEFAULT_BASES[cubic_index], coefficient_bound
            )
            if positive_towers
            else {
                name: random_polynomial(rng, coefficient_bound)
                for name in names
            }
        )
        try:
            candidate = generate_quadratic_tower(
                DEFAULT_BASES[cubic_index],
                parameters,
                target_t=0,
                label_probability=0.0,
                cluster_id=f"base{cubic_index}:seed{seed}",
            )
        except (ValueError, RuntimeError):
            continue
        if candidate.candidate_hash in seen:
            continue
        seen.add(candidate.candidate_hash)
        record = candidate.to_json()
        record["experiment_seed"] = seed
        record["experiment_attempt"] = attempt
        records.append(record)
        if len(records) >= limit:
            break
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--attempts", type=int, default=500)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--seed", type=int, default=248240713)
    parser.add_argument("--coefficient-bound", type=int, default=2)
    parser.add_argument(
        "--positive-towers",
        action="store_true",
        help="use square-plus-positive layers over totally real cubics (r=24)",
    )
    args = parser.parse_args()
    records = generate_experiment(
        attempts=args.attempts,
        limit=args.limit,
        seed=args.seed,
        coefficient_bound=args.coefficient_bound,
        positive_towers=args.positive_towers,
    )
    Path(args.output).write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records)
        + ("\n" if records else ""),
        encoding="utf-8",
    )
    print(json.dumps({
        "attempts": args.attempts,
        "generated": len(records),
        "seed": args.seed,
        "positive_towers": args.positive_towers,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
