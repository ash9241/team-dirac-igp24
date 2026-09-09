#!/usr/bin/env python3
"""Discover nontrivial rational character lifts from small linear norms.

For a catalog field ``K = Q(alpha)``, ``Norm(alpha-c)=f(c)``.  A positive
squareclass supported on the ramified primes is a cheap candidate for a
quadratic character of the normal closure.  We build one exact Kummer lift
and test it against every GAP character-kernel overgroup for the known 12T
quotient.  A seed is retained only when exactly one overgroup admits a unique
maximal-subgroup descent, avoiding heuristic target assignments.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

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
DEFAULT_OUTPUT = DATA / "linear_character_discoveries.jsonl"
DEFAULT_CHECKED = DATA / "linear_character_discoveries_checked.json"


def candidate_linear_seeds(
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
    map_path: str | Path = DEFAULT_MAP,
    shift_min: int = -50,
    shift_max: int = 50,
    bases: Iterable[int] = (),
) -> list[dict[str, Any]]:
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
        if len(targets) <= 2:
            continue
        coefficients = tuple(int(value) for value in field["coefficients"])
        disc_abs = int(field["disc_abs"])
        disc_class = _squarefree_part(disc_abs)
        seen_classes: set[int] = set()
        for shift in range(int(shift_min), int(shift_max) + 1):
            norm = sum(value * shift**power for power, value in enumerate(coefficients))
            if not norm:
                continue
            norm_class = _squarefree_part(norm)
            # A quadratic subfield of a totally real normal closure is real,
            # ramifies only at primes already ramified in the base closure,
            # and is neither the trivial nor permutation-sign character here.
            if (
                norm_class <= 1
                or norm_class == disc_class
                or norm_class in seen_classes
                or disc_abs % norm_class
            ):
                continue
            seen_classes.add(norm_class)
            seeds.append({
                "base_t": base_t,
                "label": str(field["label"]),
                "disc_abs": disc_abs,
                "base_coefficients": list(coefficients),
                "shift": shift,
                "norm_squareclass": norm_class,
                "candidate_target_ts": sorted({int(row["target_t"]) for row in targets}),
            })
    return seeds


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
    parser.add_argument("--shift-min", type=int, default=-50)
    parser.add_argument("--shift-max", type=int, default=50)
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--options", type=int, default=3)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=10000)
    args = parser.parse_args()

    if args.shift_min > args.shift_max:
        parser.error("--shift-min must not exceed --shift-max")
    seeds = candidate_linear_seeds(
        catalog_path=args.catalog,
        map_path=args.map,
        shift_min=args.shift_min,
        shift_max=args.shift_max,
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
    discovered_keys = {
        f"{row['label']}|{row['shift']}|{row['norm_squareclass']}"
        for row in discoveries
    }

    for index, seed in enumerate(seeds, 1):
        key = f"{seed['label']}|{seed['shift']}|{seed['norm_squareclass']}"
        if key in checked:
            continue
        family = family_from_integral_basis_seed(
            family=f"calibrate_linear_12t{seed['base_t']}",
            base_t=int(seed["base_t"]),
            target_t=int(seed["candidate_target_ts"][0]),
            expected_norm_squareclass=int(seed["norm_squareclass"]),
            base_coefficients=seed["base_coefficients"],
            seed_coefficients=(-int(seed["shift"]), 1),
            target_root_counts=(12,),
            base_root_count=12,
        )
        successes: list[dict[str, Any]] = []
        error: str | None = None
        try:
            options = enumerate_integral_basis_generators(
                family,
                options_per_signature=max(1, args.options),
            )[12]
            spec = None
            failures: list[str] = []
            for option in options:
                try:
                    spec = build_integral_basis_lift(
                        family,
                        option,
                        coefficient_limit=args.coefficient_limit,
                    )
                    break
                except Exception as exc:
                    failures.append(str(exc))
            if spec is None:
                raise ValueError("; ".join(failures[-3:]) or "no lift option")
            frobenius = _pari_frobenius_types(
                spec.coefficients,
                prime_limit=args.prime_limit,
            )
            for target_t in seed["candidate_target_ts"]:
                try:
                    terminal_t, _, _, chain = classify_transitive_subgroup(
                        spec.coefficients,
                        int(target_t),
                        prime_limit=args.prime_limit,
                        frobenius=frobenius,
                    )
                except Exception:
                    continue
                successes.append({
                    "starting_target_t": int(target_t),
                    "pilot_terminal_t": int(terminal_t),
                    "pilot_chain": list(chain),
                })
        except Exception as exc:
            error = str(exc)

        distinct_starts = {row["starting_target_t"] for row in successes}
        calibrated = len(distinct_starts) == 1
        if calibrated and key not in discovered_keys:
            result = dict(seed)
            result.update(successes[0])
            result["calibration_kind"] = "unique_character_overgroup_descent"
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
            "event": "linear_character_calibration",
            "index": index,
            "total": len(seeds),
            "base_t": seed["base_t"],
            "shift": seed["shift"],
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
