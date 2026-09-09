#!/usr/bin/env python3
"""Discover exact character lifts by solving supported norm equations.

Linear and short-product searches only see generators with special power-
basis shapes. PARI's ``bnfisintnorm`` can instead solve

    Norm_{K/Q}(h) = d s^2

directly, where ``d`` is a signed squareclass supported on the discriminant of
the degree-12 field. Varying the small prime ``s`` matters: two elements with
the same rational norm squareclass can generate different Kummer modules, and
a prime-square twist can turn a deficient lift into the full character kernel.

Every retained seed is calibrated against the exact GAP character-kernel map,
so a norm equation alone is never treated as a target proof. Negative norms
naturally populate the complementary root signatures ``r = 2 mod 4``.
"""

from __future__ import annotations

import argparse
import ast
import json
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

from sympy import factorint

from routeA.build_catalog_character_campaign import (
    DEFAULT_CATALOG,
    DEFAULT_MAP,
    load_jsonl,
)
from routeA.build_t00035_campaign import _squarefree_part
from routeA.constructions.compositum_8x3 import (
    DEFAULT_GP,
    _run_gp,
    polynomial_expression,
)
from routeA.discover_product_characters import (
    _seed_key,
    calibrate_character_seed,
)


DATA = Path(__file__).resolve().parent / "data"
DEFAULT_OUTPUT = DATA / "norm_equation_character_discoveries.jsonl"
DEFAULT_CHECKED = DATA / "norm_equation_character_discoveries_checked.json"
DEFAULT_SCALES = (
    1, 2, 3, 5, 7, 11, 13, 17, 19, 23,
    29, 31, 37, 41, 43, 47, 53, 59, 61,
)


def supported_squareclasses(
    discriminant: int,
    *,
    include_negative: bool = True,
    maximum_primes: int = 9,
) -> list[int]:
    """Return nontrivial signed classes supported on ``discriminant``.

    The field-discriminant class itself is the ordinary permutation-sign
    character and is already harvested by the catalog campaign, so it is
    omitted here together with the trivial class.
    """

    discriminant = abs(int(discriminant))
    primes = sorted(int(prime) for prime in factorint(discriminant))
    if len(primes) > int(maximum_primes):
        raise ValueError(
            f"discriminant has {len(primes)} prime factors, above cap "
            f"{int(maximum_primes)}"
        )
    sign_class = abs(_squarefree_part(discriminant))
    values: list[int] = []
    for mask in range(0, 1 << len(primes)):
        value = 1
        for index, prime in enumerate(primes):
            if mask & (1 << index):
                value *= prime
        if value not in {1, sign_class}:
            values.append(value)
        if include_negative:
            values.append(-value)
    return sorted(values, key=lambda value: (abs(value), value < 0))


def _parse_norm_rows(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.startswith("NORM|"):
            continue
        _, raw_class, raw_scale, raw_height, raw_num, raw_den = line.split("|", 5)
        numerators = ast.literal_eval(raw_num)
        denominators = ast.literal_eval(raw_den)
        coefficients = [
            Fraction(int(numerator), int(denominator))
            for numerator, denominator in zip(numerators, denominators)
        ]
        while len(coefficients) > 1 and coefficients[-1] == 0:
            coefficients.pop()
        rows.append({
            "norm_squareclass": int(raw_class),
            "norm_scale": int(raw_scale),
            "height": int(raw_height),
            "seed_coefficients": coefficients,
        })
    return rows


def solve_supported_norms(
    field: dict[str, Any],
    squareclasses: Sequence[int],
    *,
    scales: Sequence[int] = DEFAULT_SCALES,
    solutions_per_scale: int = 1,
    options_per_squareclass: int = 12,
    equation_timeout: float = 10,
    initialization_timeout: float = 180,
    timeout: float = 1200,
    gp: str = DEFAULT_GP,
) -> list[dict[str, Any]]:
    """Solve a batch of exact norm equations in one PARI session."""

    if solutions_per_scale < 1 or options_per_squareclass < 1:
        raise ValueError("solution limits must be positive")
    equation_seconds = int(equation_timeout)
    initialization_seconds = int(initialization_timeout)
    if equation_seconds < 1 or initialization_seconds < 1:
        raise ValueError("norm-equation timeouts must be positive")
    classes_text = ",".join(str(int(value)) for value in squareclasses)
    scales_text = ",".join(str(abs(int(value))) for value in scales if int(value))
    if not classes_text or not scales_text:
        return []
    base = polynomial_expression(field["coefficients"], "x")
    # GP terminates an unfinished ``for`` body at a physical newline, so the
    # nested loop must remain one expression even though it is assembled from
    # readable Python fragments here.
    loop = (
        "for(i=1,#classes,d=classes[i];"
        "for(j=1,#scales,s=scales[j];n=d*s^2;"
        f"sol=iferr(alarm({equation_seconds},bnfisintnorm(b,n)),E,[]);"
        f"for(k=1,min({int(solutions_per_scale)},#sol),h=lift(sol[k]);"
        "vn=vector(12,q,numerator(polcoef(h,q-1)));"
        "vd=vector(12,q,denominator(polcoef(h,q-1)));"
        "height=vecmax(vector(12,q,max(abs(vn[q]),vd[q])));"
        "print(\"NORM|\",d,\"|\",s,\"|\",height,\"|\",vn,\"|\",vd))))"
    )
    script = (
        f"x='x; f={base}; classes=[{classes_text}]; scales=[{scales_text}];\n"
        'if(!polisirreducible(f),print("ERROR|base-reducible");quit(1));\n'
        f"b=iferr(alarm({initialization_seconds},bnfinit(f,1)),E,0);\n"
        'if(b==0,print("ERROR|bnfinit-failed-or-timed-out");quit);\n'
        f"{loop};quit;\n"
    )
    output = _run_gp(script, gp=gp, timeout=timeout)
    if "ERROR|" in output:
        detail = next(
            line.split("|", 1)[1]
            for line in output.splitlines()
            if line.startswith("ERROR|")
        )
        raise ValueError(detail)
    rows = _parse_norm_rows(output)
    by_class: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_class.setdefault(int(row["norm_squareclass"]), []).append(row)
    selected: list[dict[str, Any]] = []
    for _, options in sorted(by_class.items()):
        # One representative per scale keeps arithmetic diversity. A larger
        # prime-square twist can have the full Kummer orbit even when the
        # smallest exact-norm solution is deficient.
        options.sort(key=lambda row: (int(row["norm_scale"]), int(row["height"])))
        selected.extend(options[: int(options_per_squareclass)])
    return selected


def candidate_norm_equation_seeds(
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
    map_path: str | Path = DEFAULT_MAP,
    bases: Iterable[int] = (),
    scales: Sequence[int] = DEFAULT_SCALES,
    solutions_per_scale: int = 1,
    options_per_squareclass: int = 12,
    maximum_primes: int = 9,
    equation_timeout: float = 10,
    initialization_timeout: float = 180,
) -> list[dict[str, Any]]:
    """Return exact rational power-basis seeds ready for GAP calibration."""

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
        try:
            classes = supported_squareclasses(
                int(field["disc_abs"]),
                maximum_primes=int(maximum_primes),
            )
            solutions = solve_supported_norms(
                field,
                classes,
                scales=scales,
                solutions_per_scale=solutions_per_scale,
                options_per_squareclass=options_per_squareclass,
                equation_timeout=equation_timeout,
                initialization_timeout=initialization_timeout,
            )
        except (RuntimeError, ValueError) as error:
            print(json.dumps({
                "event": "norm_equation_field_error",
                "base_t": base_t,
                "label": field.get("label"),
                "error": str(error),
            }))
            continue
        target_ts = sorted({int(row["target_t"]) for row in targets})
        for solution in solutions:
            seed = dict(solution)
            seed.update({
                "base_t": base_t,
                "label": str(field["label"]),
                "disc_abs": int(field["disc_abs"]),
                "base_coefficients": [int(value) for value in field["coefficients"]],
                "candidate_target_ts": target_ts,
                "seed_coefficients": [
                    str(value) for value in solution["seed_coefficients"]
                ],
                "seed_tag": (
                    f"normeq_{solution['norm_squareclass']}_"
                    f"s{solution['norm_scale']}_h{solution['height']}"
                ),
            })
            output.append(seed)
        print(json.dumps({
            "event": "norm_equation_field",
            "base_t": base_t,
            "label": field.get("label"),
            "squareclasses": len(classes),
            "solutions": len(solutions),
        }))
    return sorted(
        output,
        key=lambda row: (
            int(row["base_t"]),
            abs(int(row["norm_squareclass"])),
            int(row["norm_squareclass"]) < 0,
            int(row["norm_scale"]),
            int(row["height"]),
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
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--scale", action="append", type=int, default=[])
    parser.add_argument("--solutions-per-scale", type=int, default=1)
    parser.add_argument("--options-per-squareclass", type=int, default=12)
    parser.add_argument("--maximum-primes", type=int, default=9)
    parser.add_argument("--equation-timeout", type=float, default=10)
    parser.add_argument("--initialization-timeout", type=float, default=180)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--options", type=int, default=4)
    parser.add_argument("--max-lifts", type=int, default=12)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=10000)
    args = parser.parse_args()

    scales = tuple(args.scale) if args.scale else DEFAULT_SCALES
    seeds = candidate_norm_equation_seeds(
        catalog_path=args.catalog,
        map_path=args.map,
        bases=args.base,
        scales=scales,
        solutions_per_scale=args.solutions_per_scale,
        options_per_squareclass=args.options_per_squareclass,
        maximum_primes=args.maximum_primes,
        equation_timeout=args.equation_timeout,
        initialization_timeout=args.initialization_timeout,
    )
    if args.limit is not None:
        seeds = seeds[: max(0, int(args.limit))]

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
            result["calibration_kind"] = "exact_supported_norm_equation"
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
            "event": "norm_equation_character_calibration",
            "index": index,
            "total": len(seeds),
            "base_t": seed["base_t"],
            "norm_squareclass": seed["norm_squareclass"],
            "norm_scale": seed["norm_scale"],
            "calibrated": calibrated,
            "successes": successes,
            "error": error,
            "discoveries": len(discoveries),
        }))
    print(json.dumps({
        "candidate_seeds": len(seeds),
        "checked": len(checked),
        "discoveries": len(discoveries),
        "output": str(args.output),
    }))


if __name__ == "__main__":
    main()
