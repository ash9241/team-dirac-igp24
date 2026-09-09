#!/usr/bin/env python3
"""Discover character lifts from arbitrary supported products of linear factors.

The quadratic and cubic product search finds only very short relations among
the squareclasses ``f(c)``.  For several valuable degree-12 groups the first
relation supported on the field discriminant has larger weight.  This module
finds those relations exactly: unramified prime factors (and the sign) are
encoded as vectors over ``GF(2)``, their dependency space is computed by
Gaussian elimination, and the kernel is enumerated without testing all
subsets of shifts.

Every resulting seed is still calibrated against the exact GAP
character-kernel map before it is retained.  The output schema matches
``build_discovered_character_campaign``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from sympy import factorint

from routeA.build_catalog_character_campaign import (
    DEFAULT_CATALOG,
    DEFAULT_MAP,
    load_jsonl,
)
from routeA.build_t00035_campaign import _squarefree_part
from routeA.discover_character_closure import _multiply_mod
from routeA.discover_product_characters import (
    _seed_key,
    calibrate_character_seed,
)


DATA = Path(__file__).resolve().parent / "data"
DEFAULT_OUTPUT = DATA / "kernel_product_character_discoveries.jsonl"
DEFAULT_CHECKED = DATA / "kernel_product_character_discoveries_checked.json"


def dependency_basis(column_vectors: Sequence[int]) -> list[int]:
    """Return an independent basis of column dependencies over ``GF(2)``.

    Each returned integer is a bit mask on the input columns whose vector
    XOR is zero.  Processing columns from left to right makes every emitted
    dependency contain a fresh column, so the dependency masks are themselves
    independent.
    """

    pivots: dict[int, tuple[int, int]] = {}
    dependencies: list[int] = []
    for index, raw_vector in enumerate(column_vectors):
        vector = int(raw_vector)
        combination = 1 << index
        while vector:
            pivot = vector.bit_length() - 1
            previous = pivots.get(pivot)
            if previous is None:
                pivots[pivot] = vector, combination
                break
            vector ^= previous[0]
            combination ^= previous[1]
        if not vector:
            dependencies.append(combination)
    return dependencies


def enumerate_kernel_masks(
    basis: Sequence[int],
    *,
    maximum_relations: int = 1 << 20,
) -> Iterable[int]:
    """Enumerate every nonzero dependency using Gray-code updates."""

    dimension = len(basis)
    relation_count = (1 << dimension) - 1
    if relation_count > int(maximum_relations):
        raise ValueError(
            f"kernel has {relation_count} relations, above cap "
            f"{int(maximum_relations)}"
        )
    previous_gray = 0
    relation = 0
    for counter in range(1, 1 << dimension):
        gray = counter ^ (counter >> 1)
        changed = gray ^ previous_gray
        relation ^= int(basis[changed.bit_length() - 1])
        previous_gray = gray
        yield relation


def _product_seed(shifts: Sequence[int], modulus: Sequence[int]) -> tuple[int, ...]:
    seed: tuple[int, ...] = (1,)
    for shift in shifts:
        seed = _multiply_mod(seed, (-int(shift), 1), modulus)
    return seed


def _mask_xor(mask: int, values: Sequence[int]) -> int:
    output = 0
    remaining = int(mask)
    while remaining:
        bit = remaining & -remaining
        output ^= int(values[bit.bit_length() - 1])
        remaining ^= bit
    return output


def candidate_kernel_product_seeds(
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
    map_path: str | Path = DEFAULT_MAP,
    shift_min: int = -10,
    shift_max: int = 10,
    minimum_degree: int = 4,
    options_per_squareclass: int = 3,
    maximum_relations: int = 1 << 20,
    bases: Iterable[int] = (),
) -> list[dict[str, Any]]:
    """Return compact discriminant-supported seeds from the exact kernel."""

    if shift_min > shift_max:
        raise ValueError("shift_min must not exceed shift_max")
    if minimum_degree < 1:
        raise ValueError("minimum_degree must be positive")
    if options_per_squareclass < 1:
        raise ValueError("options_per_squareclass must be positive")

    by_base: dict[int, list[dict[str, Any]]] = {}
    for row in load_jsonl(map_path):
        by_base.setdefault(int(row["base_t"]), []).append(row)

    wanted = {int(value) for value in bases}
    output: list[dict[str, Any]] = []
    for field in load_jsonl(catalog_path):
        base_t = int(field["base_t"])
        if wanted and base_t not in wanted:
            continue
        targets = by_base.get(base_t, [])
        if len(targets) <= 2:
            continue
        coefficients = tuple(int(value) for value in field["coefficients"])
        disc_abs = int(field["disc_abs"])
        disc_class = abs(_squarefree_part(disc_abs))

        shifts: list[int] = []
        norm_classes: list[int] = []
        factorizations: list[set[int]] = []
        all_good_primes: set[int] = set()
        all_bad_primes: set[int] = set()
        for shift in range(int(shift_min), int(shift_max) + 1):
            norm = sum(
                value * shift**power
                for power, value in enumerate(coefficients)
            )
            if not norm:
                continue
            norm_class = _squarefree_part(norm)
            primes = {int(prime) for prime in factorint(abs(norm_class))}
            good = {prime for prime in primes if disc_abs % prime == 0}
            bad = primes - good
            shifts.append(int(shift))
            norm_classes.append(int(norm_class))
            factorizations.append(primes)
            all_good_primes.update(good)
            all_bad_primes.update(bad)

        good_primes = sorted(all_good_primes)
        bad_primes = sorted(all_bad_primes)
        good_index = {prime: index for index, prime in enumerate(good_primes)}
        # Bit zero records the sign, so only positive products lie in the
        # dependency kernel.  Unramified primes begin at bit one.
        bad_index = {prime: index + 1 for index, prime in enumerate(bad_primes)}
        bad_vectors: list[int] = []
        good_vectors: list[int] = []
        for norm_class, primes in zip(norm_classes, factorizations):
            bad_vector = int(norm_class < 0)
            good_vector = 0
            for prime in primes:
                if prime in bad_index:
                    bad_vector ^= 1 << bad_index[prime]
                else:
                    good_vector ^= 1 << good_index[prime]
            bad_vectors.append(bad_vector)
            good_vectors.append(good_vector)

        basis = dependency_basis(bad_vectors)
        best: dict[int, list[tuple[tuple[Any, ...], int]]] = {}
        try:
            relations = enumerate_kernel_masks(
                basis,
                maximum_relations=int(maximum_relations),
            )
            for mask in relations:
                # The benchmark controller still supports the system Python
                # 3.9 shipped by older macOS releases.
                degree = bin(int(mask)).count("1")
                if degree < int(minimum_degree):
                    continue
                good_vector = _mask_xor(mask, good_vectors)
                norm_class = 1
                for index, prime in enumerate(good_primes):
                    if good_vector & (1 << index):
                        norm_class *= int(prime)
                if norm_class <= 1 or norm_class == disc_class:
                    continue
                chosen = tuple(
                    shifts[index]
                    for index in range(len(shifts))
                    if mask & (1 << index)
                )
                rank = (
                    degree,
                    max(abs(value) for value in chosen),
                    sum(abs(value) for value in chosen),
                    chosen,
                )
                rows = best.setdefault(int(norm_class), [])
                rows.append((rank, int(mask)))
                rows.sort(key=lambda item: item[0])
                del rows[int(options_per_squareclass):]
        except ValueError:
            # A very large kernel is better handled with a wider targeted run
            # than by silently truncating a supposedly exact enumeration.
            continue

        target_ts = sorted({int(row["target_t"]) for row in targets})
        for norm_class, choices in sorted(best.items()):
            for _, mask in choices:
                chosen = tuple(
                    shifts[index]
                    for index in range(len(shifts))
                    if mask & (1 << index)
                )
                output.append({
                    "base_t": base_t,
                    "label": str(field["label"]),
                    "disc_abs": disc_abs,
                    "base_coefficients": list(coefficients),
                    "factor_shifts": list(chosen),
                    "seed_coefficients": list(_product_seed(chosen, coefficients)),
                    "norm_squareclass": int(norm_class),
                    "candidate_target_ts": target_ts,
                    "kernel_dimension": len(basis),
                })
    return sorted(
        output,
        key=lambda row: (
            int(row["base_t"]),
            len(row["factor_shifts"]),
            int(row["norm_squareclass"]),
            tuple(row["factor_shifts"]),
        ),
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--checked", type=Path, default=DEFAULT_CHECKED)
    parser.add_argument("--shift-min", type=int, default=-10)
    parser.add_argument("--shift-max", type=int, default=10)
    parser.add_argument("--minimum-degree", type=int, default=4)
    parser.add_argument("--options-per-squareclass", type=int, default=3)
    parser.add_argument("--maximum-relations", type=int, default=1 << 20)
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--options", type=int, default=4)
    parser.add_argument("--max-lifts", type=int, default=12)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=10000)
    args = parser.parse_args()

    seeds = candidate_kernel_product_seeds(
        catalog_path=args.catalog,
        map_path=args.map,
        shift_min=args.shift_min,
        shift_max=args.shift_max,
        minimum_degree=args.minimum_degree,
        options_per_squareclass=args.options_per_squareclass,
        maximum_relations=args.maximum_relations,
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
            result["calibration_kind"] = "gf2_kernel_product_character"
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
            "event": "kernel_product_character_calibration",
            "index": index,
            "total": len(seeds),
            "base_t": seed["base_t"],
            "degree": len(seed["factor_shifts"]),
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
