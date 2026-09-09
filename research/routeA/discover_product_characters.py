#!/usr/bin/env python3
"""Discover rational character lifts from products of small linear factors.

For ``K = Q(alpha)`` with defining polynomial ``f``, the element

    h(alpha) = product(alpha - c_i)

has norm ``product(f(c_i))``.  Products are substantially more useful than a
linear scan: prime factors outside the discriminant can cancel in pairs,
leaving the squareclass of a genuine quadratic character of the normal
closure.  This module searches quadratic and cubic products, calibrates each
surviving squareclass against the exact GAP character-kernel map, and keeps
only seeds with one unambiguous starting overgroup.

The output schema is intentionally compatible with
``build_discovered_character_campaign``.
"""

from __future__ import annotations

import argparse
import itertools
import json
from math import gcd
from pathlib import Path
from typing import Any, Iterable, Sequence

from routeA.build_catalog_character_campaign import (
    DEFAULT_CATALOG,
    DEFAULT_MAP,
    load_jsonl,
)
from routeA.build_t00035_campaign import _squarefree_part
from routeA.constructions.integral_basis_character_lift import (
    build_integral_basis_lift,
    enumerate_integral_basis_generators,
    family_from_integral_basis_seed,
)
from routeA.constructions.nested_character_lift import (
    _pari_frobenius_types,
    classify_transitive_subgroup,
)


DATA = Path(__file__).resolve().parent / "data"
DEFAULT_OUTPUT = DATA / "product_character_discoveries.jsonl"
DEFAULT_CHECKED = DATA / "product_character_discoveries_checked.json"


def _squarefree_product(left: int, right: int) -> int:
    """Multiply two already-squarefree signed integers modulo squares."""

    common = gcd(abs(int(left)), abs(int(right)))
    return int(left) * int(right) // (common * common)


def _linear_factor_product(shifts: Sequence[int]) -> tuple[int, ...]:
    """Return ascending coefficients of ``product(x-shift)``."""

    coefficients = [1]
    for raw_shift in shifts:
        shift = int(raw_shift)
        output = [0] * (len(coefficients) + 1)
        for index, value in enumerate(coefficients):
            output[index] -= shift * value
            output[index + 1] += value
        coefficients = output
    return tuple(coefficients)


def candidate_product_seeds(
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
    map_path: str | Path = DEFAULT_MAP,
    shift_min: int = -10,
    shift_max: int = 10,
    degrees: Iterable[int] = (2, 3),
    bases: Iterable[int] = (),
) -> list[dict[str, Any]]:
    """Return one compact product seed per base and supported squareclass."""

    requested_degrees = tuple(sorted({int(value) for value in degrees}))
    if not requested_degrees or requested_degrees[0] < 2:
        raise ValueError("product degrees must be at least two")
    if shift_min > shift_max:
        raise ValueError("shift_min must not exceed shift_max")

    character_rows = load_jsonl(map_path)
    by_base: dict[int, list[dict[str, Any]]] = {}
    for row in character_rows:
        by_base.setdefault(int(row["base_t"]), []).append(row)

    wanted = {int(value) for value in bases}
    seeds: list[dict[str, Any]] = []
    for field in load_jsonl(catalog_path):
        base_t = int(field["base_t"])
        if wanted and base_t not in wanted:
            continue
        targets = by_base.get(base_t, [])
        # With no character beyond sign/trivial, a new supported positive
        # squareclass cannot add a character-kernel target.
        if len(targets) <= 2:
            continue
        coefficients = tuple(int(value) for value in field["coefficients"])
        disc_abs = int(field["disc_abs"])
        disc_class = abs(_squarefree_part(disc_abs))
        linear: list[tuple[int, int]] = []
        for shift in range(int(shift_min), int(shift_max) + 1):
            norm = sum(
                value * shift**power
                for power, value in enumerate(coefficients)
            )
            if norm:
                linear.append((shift, _squarefree_part(norm)))

        # Prefer lower degree, then smaller coefficients/shifts.  Distinct
        # shifts keep h from being a square by construction.
        best: dict[int, tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]] = {}
        for degree in requested_degrees:
            for factors in itertools.combinations(linear, degree):
                norm_class = 1
                shifts: list[int] = []
                for shift, factor_class in factors:
                    shifts.append(int(shift))
                    norm_class = _squarefree_product(norm_class, factor_class)
                if (
                    norm_class <= 1
                    or norm_class == disc_class
                    or disc_abs % norm_class
                ):
                    continue
                seed = _linear_factor_product(shifts)
                rank = (
                    degree,
                    max(abs(value) for value in seed),
                    max(abs(value) for value in shifts),
                    sum(abs(value) for value in shifts),
                )
                current = best.get(norm_class)
                if current is None or rank < current[0]:
                    best[norm_class] = (rank, tuple(shifts), seed)

        for norm_class, (_, shifts, seed) in sorted(best.items()):
            seeds.append({
                "base_t": base_t,
                "label": str(field["label"]),
                "disc_abs": disc_abs,
                "base_coefficients": list(coefficients),
                "factor_shifts": list(shifts),
                "seed_coefficients": list(seed),
                "norm_squareclass": int(norm_class),
                "candidate_target_ts": sorted({
                    int(row["target_t"]) for row in targets
                }),
            })
    return sorted(
        seeds,
        key=lambda row: (
            int(row["base_t"]),
            len(row["seed_coefficients"]),
            int(row["norm_squareclass"]),
        ),
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed_key(row: dict[str, Any]) -> str:
    return (
        f"{row['label']}|{','.join(map(str, row['seed_coefficients']))}|"
        f"{row['norm_squareclass']}"
    )


def calibrate_character_seed(
    seed: dict[str, Any],
    *,
    options_per_signature: int = 2,
    max_lifts: int = 6,
    coefficient_limit: int = 10**120,
    prime_limit: int = 10000,
) -> tuple[list[dict[str, Any]], str | None]:
    """Calibrate one arithmetic squareclass against exact GAP overgroups."""

    norm_squareclass = int(seed["norm_squareclass"])
    target_roots = tuple(range(2 if norm_squareclass < 0 else 0, 25, 4))
    family = family_from_integral_basis_seed(
        family=f"calibrate_product_12t{seed['base_t']}",
        base_t=int(seed["base_t"]),
        target_t=int(seed["candidate_target_ts"][0]),
        expected_norm_squareclass=norm_squareclass,
        base_coefficients=seed["base_coefficients"],
        seed_coefficients=seed["seed_coefficients"],
        target_root_counts=target_roots,
        base_root_count=12,
    )
    successes: list[dict[str, Any]] = []
    error: str | None = None
    try:
        by_signature = enumerate_integral_basis_generators(
            family,
            options_per_signature=max(1, int(options_per_signature)),
            allow_missing_signatures=True,
        )
        options = sorted(
            (option for rows in by_signature.values() for option in rows),
            key=lambda option: (
                option.height,
                option.target_r,
                option.unit_mask,
            ),
        )
        failures: list[str] = []
        calibrated = False
        for option in options[: max(1, int(max_lifts))]:
            try:
                spec = build_integral_basis_lift(
                    family,
                    option,
                    coefficient_limit=int(coefficient_limit),
                )
                frobenius = _pari_frobenius_types(
                    spec.coefficients,
                    prime_limit=int(prime_limit),
                )
            except Exception as exc:
                failures.append(str(exc))
                continue
            option_successes: list[dict[str, Any]] = []
            for target_t in seed["candidate_target_ts"]:
                try:
                    terminal_t, _, _, chain = classify_transitive_subgroup(
                        spec.coefficients,
                        int(target_t),
                        prime_limit=int(prime_limit),
                        frobenius=frobenius,
                    )
                except Exception:
                    continue
                option_successes.append({
                    "starting_target_t": int(target_t),
                    "pilot_terminal_t": int(terminal_t),
                    "pilot_chain": list(chain),
                })
            option_starts = {
                row["starting_target_t"] for row in option_successes
            }
            if len(option_starts) == 1:
                successes = option_successes
                calibrated = True
                break
            if len(option_successes) > len(successes):
                successes = option_successes
        if not options:
            raise ValueError("no unit-signature lift option")
        if not calibrated and failures and not successes:
            error = "; ".join(failures[-3:])
    except Exception as exc:
        error = str(exc)
    return successes, error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--checked", type=Path, default=DEFAULT_CHECKED)
    parser.add_argument("--shift-min", type=int, default=-10)
    parser.add_argument("--shift-max", type=int, default=10)
    parser.add_argument("--degree", action="append", type=int, default=[])
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--options", type=int, default=2)
    parser.add_argument(
        "--max-lifts",
        type=int,
        default=6,
        help="maximum unit-twist lift options tried for each squareclass",
    )
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=10000)
    args = parser.parse_args()

    seeds = candidate_product_seeds(
        catalog_path=args.catalog,
        map_path=args.map,
        shift_min=args.shift_min,
        shift_max=args.shift_max,
        degrees=args.degree or (2, 3),
        bases=args.base,
    )
    if args.limit is not None:
        seeds = seeds[: max(0, args.limit)]

    discoveries = load_jsonl(args.output)
    checked = (
        set(json.loads(args.checked.read_text(encoding="utf-8")))
        if args.checked.exists()
        else set()
    )
    discovered_keys = {_seed_key(row) for row in discoveries}

    for index, seed in enumerate(seeds, 1):
        key = _seed_key(seed)
        if key in checked:
            continue
        successes, error = calibrate_character_seed(
            seed,
            options_per_signature=args.options,
            max_lifts=args.max_lifts,
            coefficient_limit=args.coefficient_limit,
            prime_limit=args.prime_limit,
        )

        distinct_starts = {row["starting_target_t"] for row in successes}
        calibrated = len(distinct_starts) == 1
        if calibrated and key not in discovered_keys:
            result = dict(seed)
            result.update(successes[0])
            result["calibration_kind"] = "unique_product_character_overgroup_descent"
            discoveries.append(result)
            discovered_keys.add(key)
            _write_jsonl(args.output, discoveries)
        checked.add(key)
        args.checked.parent.mkdir(parents=True, exist_ok=True)
        args.checked.write_text(
            json.dumps(sorted(checked), indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "event": "product_character_calibration",
            "index": index,
            "total": len(seeds),
            "base_t": seed["base_t"],
            "factor_shifts": seed["factor_shifts"],
            "norm_squareclass": seed["norm_squareclass"],
            "calibrated": calibrated,
            "successes": successes,
            "error": error,
            "discoveries": len(discoveries),
        }, sort_keys=True), flush=True)

    print(json.dumps({
        "candidate_seeds": len(seeds),
        "checked": len(checked),
        "discoveries": len(discoveries),
        "output": str(args.output),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
