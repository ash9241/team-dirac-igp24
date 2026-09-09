#!/usr/bin/env python3
"""Generate validity-certified quartic tower probes without API credentials."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from routeA.empirical_recipes import (
    ES,
    cubic_tower_job,
    run_gp,
    s3_cubic_tower_job,
    sextic_quartic_job,
)
from routeA.ledger import candidate_hash, canonical_coefficients


# Verified PARI ``F_36(6)`` sextics.  This is GAP's 6T10 action, the quotient
# required by the highest-value block-4 architecture gaps (including 24T24429).
SEXTIC_6T10_BASES = (
    (-16, 12, 9, -4, -6, 0, 1),
    (-16, -12, 9, 4, -6, 0, 1),
    (-4, -6, 9, -2, 6, 0, 1),
    (-4, 18, 9, 6, 6, 0, 1),
    (-4, -18, 9, -6, 6, 0, 1),
    (-13, -12, 9, 4, -6, 0, 1),
    (-4, 12, 9, 4, 6, 0, 1),
)

# Pair-sum resolvents of S4 quartics.  PARI identifies this action as
# ``S_4(6d)``, GAP 6T7.  It is the largest remaining block-4 opportunity by
# a wide margin.
SEXTIC_6T7_BASES = (
    (-1, 0, 4, 0, 0, 0, 1),
    (-1, 0, -4, 0, 0, 0, 1),
)

# Pair-sum resolvents of totally-real S4 quartics.  Every entry is a
# totally-real ``S_4(6d)``/6T7 sextic, so quartic fibers can reach all the way
# to the r=24 signature instead of being capped at r=8.
SEXTIC_6T7_REAL_BASES = (
    (-8, -25, 13, 23, -9, -3, 1),
    (26, -130, 102, 55, -25, -3, 1),
    (-4, 0, 65, 0, -26, 0, 1),
    (26, 119, 89, -59, -27, 3, 1),
    (71, -6, -106, 45, 15, -9, 1),
    (28, 96, -280, 64, 32, -12, 1),
    (-9, 0, 88, 0, -20, 0, 1),
    (12, -102, 78, 47, -21, -3, 1),
    (19, -129, 101, 55, -25, -3, 1),
    (-64, 0, 141, 0, -26, 0, 1),
    (2, -39, -49, 3, 23, 9, 1),
    (81, 3, -125, -57, 13, 9, 1),
)

# Quadratic extensions of an S3 cubic with independent conjugate
# squareclasses.  PARI identifies every entry as ``2S_4(6)``, GAP 6T11.
# This is the second-largest remaining block-4 opportunity after 6T7.
SEXTIC_6T11_BASES = (
    (8, 0, 16, 0, 10, 0, 1),
    (5, 0, 11, 0, 8, 0, 1),
    (8, 0, 8, 0, 6, 0, 1),
    (11, 0, 7, 0, 4, 0, 1),
    (40, 0, 32, 0, 10, 0, 1),
    (19, 0, 21, 0, 8, 0, 1),
    (1, 0, 5, 0, 4, 0, 1),
    (-8, 0, 0, 0, 2, 0, 1),
    (53, 0, 37, 0, 10, 0, 1),
    (25, 0, 20, 0, 7, 0, 1),
    (1, 0, -1, 0, 7, 0, 1),
    (5, 0, -1, 0, 3, 0, 1),
    # Totally-real S3 sources add quotient signatures 2, 4, and 6.
    (-353, 0, -76, 0, 4, 0, 1),
    (-107, 0, 12, 0, 12, 0, 1),
    (-320, 0, -32, 0, 12, 0, 1),
    (-143, 0, 3, 0, 12, 0, 1),
    (256, 0, -64, 0, -20, 0, 1),
    (58, 0, -63, 0, -12, 0, 1),
    (109, 0, -83, 0, -18, 0, 1),
    (277, 0, -39, 0, -18, 0, 1),
    (-14, 0, 71, 0, -21, 0, 1),
    (-23, 0, 66, 0, -16, 0, 1),
    (-5, 0, 23, 0, -10, 0, 1),
    (-27, 0, 101, 0, -20, 0, 1),
    (-6, 0, 33, 0, -12, 0, 1),
    (-80, 0, 327, 0, -36, 0, 1),
    (-7, 0, 45, 0, -14, 0, 1),
    (-35, 0, 195, 0, -28, 0, 1),
)
SEXTIC_6T11_REAL_BASES = SEXTIC_6T11_BASES[-8:]

# Quadratic extensions of a cyclic cubic with independent conjugate
# squareclasses.  These are the full ``2 wr C3`` action, GAP 6T6.
SEXTIC_6T6_BASES = (
    (-27, 0, -45, 0, -6, 0, 1),
    (-31, 0, -25, 0, -1, 0, 1),
    (-11, 0, -1, 0, 4, 0, 1),
)
SEXTIC_6T6_REAL_BASES = (
    (-7, 0, 127, 0, -113, 0, 1),
    (-11, 0, 69, 0, -108, 0, 1),
    (-7, 0, 32, 0, -37, 0, 1),
    (-31, 0, 89, 0, -58, 0, 1),
    (-77, 0, 174, 0, -79, 0, 1),
    (-151, 0, 287, 0, -100, 0, 1),
)

# Direct products of S3 with a quadratic character.  PARI identifies these
# as ``D(6) = S(3)[x]2``, GAP 6T3.
SEXTIC_6T3_BASES = (
    (8, 0, 8, 0, 2, 0, 1),
    (-8, 0, 8, 0, 6, 0, 1),
    (1, 0, -2, 0, 5, 0, 1),
    (8, 0, -12, 0, 4, 0, 1),
    (1, 0, 1, 0, 2, 0, 1),
    (8, 0, 4, 0, 4, 0, 1),
    (1, 0, 2, 0, 1, 0, 1),
    (-8, 0, 4, 0, -4, 0, 1),
    (1, 0, 2, 0, -3, 0, 1),
    # Positive quadratic characters over totally-real S3 cubics.
    (-8, 0, 64, 0, -16, 0, 1),
    (-27, 0, 144, 0, -24, 0, 1),
    (-32, 0, 52, 0, -22, 0, 1),
    (-512, 0, 356, 0, -38, 0, 1),
    (-8, 0, 100, 0, -20, 0, 1),
    (-72, 0, 88, 0, -26, 0, 1),
)
SEXTIC_6T3_REAL_BASES = SEXTIC_6T3_BASES[-6:]


def _polynomial(values: tuple[int, ...] | list[int], variable: str = "x") -> str:
    return "(" + "+".join(
        f"({int(coefficient)})*{variable}^{degree}"
        for degree, coefficient in enumerate(values)
        if int(coefficient)
    ) + ")"


def _random_sextic_element() -> str:
    values = [random.randint(-2, 2) for _ in range(6)]
    if not any(values):
        values[random.randrange(6)] = 1
    return _polynomial(values)


def _targeted_sextic_quartic_job(
    index: int,
    *,
    bases: tuple[tuple[int, ...], ...],
    family: str,
) -> tuple[dict[str, Any], str]:
    base = random.choice(bases)
    base_expression = _polynomial(base)
    a, b, scale, h = (
        _random_sextic_element() for _ in range(4)
    )
    squareclass = random.choice(ES)
    form_roll = random.random()
    if form_roll < 0.08:
        form = "pure4"
        quartic = f"Q4=y^4-({a});"
    elif form_roll < 0.23:
        form = "c4"
        quartic = (
            f"AA={a};BB={b};gg={scale};DD=AA^2+BB^2;"
            "Q4=y^4-2*gg*DD*y^2+gg^2*DD*BB^2;"
        )
    elif form_roll < 0.38:
        form = "v4-entangled"
        quartic = (
            f"uu={a};hh={h};"
            f"Q4=y^4-2*uu*(1+({squareclass})*hh^2)*y^2"
            f"+uu^2*(1-({squareclass})*hh^2)^2;"
        )
    else:
        kernel_scale = random.choice([1, 1, 1, 2, 3, 5])
        form = f"d4-k{kernel_scale}"
        quartic = (
            f"AA={a};BB={b};gg={scale};"
            f"DD=({kernel_scale})*(AA^2+({squareclass})*BB^2);"
            f"Q4=y^4-2*gg*DD*y^2+gg^2*({squareclass})"
            f"*({kernel_scale})*DD*BB^2;"
        )
    dial = {
        "fam": family,
        "base_coefficients": list(base),
        "form": form,
        "e": squareclass,
        "A": a,
        "B": b,
        "g": scale,
        "h": h,
    }
    script = (
        f"f6={base_expression};"
        f"if(polisirreducible(f6),{quartic}"
        "p=subst(polresultant(f6,Q4,x),y,x);"
        "if(poldegree(p)==24&&polcoef(p,24)==1&&polisirreducible(p),"
        f"print(\"RA|{index}|\",polsturm(p),\"|\",Vec(p)),"
        f"print(\"RA|{index}|X|X\")),print(\"RA|{index}|X|X\"));"
    )
    return dial, script


def _parse_vector(raw: str) -> list[int]:
    value = raw.strip()
    if not value.startswith("[") or not value.endswith("]"):
        raise ValueError("invalid PARI vector")
    return [int(part.strip()) for part in value[1:-1].split(",")]


def generate_empirical_towers(
    *,
    attempts: int,
    limit: int,
    seed: int,
    family: str = "sextic-quartic",
    coefficient_limit: int = 10**55,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if family not in {
        "sextic-quartic",
        "sextic-6t3-quartic",
        "sextic-6t3-real-quartic",
        "sextic-6t6-quartic",
        "sextic-6t6-real-quartic",
        "sextic-6t7-quartic",
        "sextic-6t7-real-quartic",
        "sextic-6t10-quartic",
        "sextic-6t11-quartic",
        "sextic-6t11-real-quartic",
        "cubic-quartic",
        "s3-cubic-quartic",
        "mixed",
    }:
        raise ValueError(f"unsupported family: {family}")
    if attempts < 1 or limit < 1:
        raise ValueError("attempts and limit must be positive")

    random.seed(seed)
    jobs: list[dict[str, Any]] = []
    scripts: list[str] = []
    sextic_parameters = [
        (a, b, d)
        for a in range(-4, 13)
        for b in (1, -1, 2)
        for d in (2, 3, 5, 7, 13, 17)
    ]
    for index in range(attempts):
        selected_family = family
        if family == "mixed":
            selected_family = (
                "sextic-quartic" if random.random() < 0.65 else "cubic-quartic"
            )
        if selected_family == "sextic-6t3-quartic":
            dial, script = _targeted_sextic_quartic_job(
                index,
                bases=SEXTIC_6T3_BASES,
                family="SQ6T3",
            )
        elif selected_family == "sextic-6t3-real-quartic":
            dial, script = _targeted_sextic_quartic_job(
                index,
                bases=SEXTIC_6T3_REAL_BASES,
                family="SQ6T3R",
            )
        elif selected_family == "sextic-6t6-quartic":
            dial, script = _targeted_sextic_quartic_job(
                index,
                bases=SEXTIC_6T6_BASES,
                family="SQ6T6",
            )
        elif selected_family == "sextic-6t6-real-quartic":
            dial, script = _targeted_sextic_quartic_job(
                index,
                bases=SEXTIC_6T6_REAL_BASES,
                family="SQ6T6R",
            )
        elif selected_family == "sextic-6t7-quartic":
            dial, script = _targeted_sextic_quartic_job(
                index,
                bases=SEXTIC_6T7_BASES,
                family="SQ6T7",
            )
        elif selected_family == "sextic-6t7-real-quartic":
            dial, script = _targeted_sextic_quartic_job(
                index,
                bases=SEXTIC_6T7_REAL_BASES,
                family="SQ6T7R",
            )
        elif selected_family == "sextic-6t10-quartic":
            dial, script = _targeted_sextic_quartic_job(
                index,
                bases=SEXTIC_6T10_BASES,
                family="SQ6T10",
            )
        elif selected_family == "sextic-6t11-quartic":
            dial, script = _targeted_sextic_quartic_job(
                index,
                bases=SEXTIC_6T11_BASES,
                family="SQ6T11",
            )
        elif selected_family == "sextic-6t11-real-quartic":
            dial, script = _targeted_sextic_quartic_job(
                index,
                bases=SEXTIC_6T11_REAL_BASES,
                family="SQ6T11R",
            )
        elif selected_family == "sextic-quartic":
            a, b, d = random.choice(sextic_parameters)
            dial, script = sextic_quartic_job(
                index,
                a,
                b,
                d,
                random.choice(ES),
                parallel=random.random() < 0.25,
                pure4=random.random() < 0.05,
                c4=random.random() < 0.15,
                kd4=random.choice([None, None, None, 2, 3, 5]),
            )
        elif selected_family == "cubic-quartic":
            t0 = random.choice([value for value in range(-6, 16) if value != -1])
            dial, script = cubic_tower_job(
                index,
                t0,
                random.choice(ES),
                octic=random.random() < 0.35,
                tied_w=random.random() < 0.2,
                parallel=random.random() < 0.2,
                c4=random.random() < 0.15,
            )
        else:
            # Both one-real and totally-real S3 cubics are represented.  The
            # discriminant check in the recipe rejects accidental A3 bases.
            p0, q0 = random.choice((
                (-1, 1), (-2, 1), (-3, 1), (-3, 2), (-4, 1),
                (-4, 3), (-5, 1), (-5, 2), (-6, 1),
                (1, 1), (1, 2), (2, 1), (2, 3), (3, 1), (4, 1),
            ))
            dial, script = s3_cubic_tower_job(
                index,
                p0,
                q0,
                random.choice(ES),
                octic=random.random() < 0.35,
                tied_w=random.random() < 0.2,
                parallel=random.random() < 0.2,
                c4=random.random() < 0.15,
            )
        jobs.append(dial)
        scripts.append(script)

    result = run_gp("\n".join(scripts))
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:] or "PARI tower generation failed")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    valid_outputs = rejected_height = 0
    for line in result.stdout.splitlines():
        if not line.startswith("RA|"):
            continue
        _, raw_index, raw_roots, raw_vector = line.split("|", 3)
        if raw_roots == "X":
            continue
        coefficients = list(reversed(_parse_vector(raw_vector)))
        if len(coefficients) != 25 or coefficients[-1] != 1 or coefficients[0] == 0:
            continue
        if max(map(abs, coefficients)) >= coefficient_limit:
            rejected_height += 1
            continue
        serialized = canonical_coefficients(coefficients)
        key = candidate_hash(serialized)
        if key in seen:
            continue
        seen.add(key)
        valid_outputs += 1
        roots = int(raw_roots)
        dial = jobs[int(raw_index)]
        rows.append(
            {
                "candidate_hash": key,
                "coefficients": serialized,
                "construction_overgroup": "quartic-tower-exploration",
                "local_irreducible": True,
                "local_root_count": roots,
                "parameters": dial,
                "recipe_family": str(dial.get("fam") or family),
                "source_host": "credential-free-empirical-tower",
                "target_r": roots,
                "target_t": 0,
            }
        )
        if len(rows) >= limit:
            break
    return rows, {
        "attempts": attempts,
        "family": family,
        "generated": len(rows),
        "pari_valid_outputs_seen": valid_outputs,
        "rejected_height": rejected_height,
        "seed": seed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--attempts", type=int, default=3000)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=248240801)
    parser.add_argument(
        "--family",
        choices=(
            "sextic-quartic",
            "sextic-6t3-quartic",
            "sextic-6t3-real-quartic",
            "sextic-6t6-quartic",
            "sextic-6t6-real-quartic",
            "sextic-6t7-quartic",
            "sextic-6t7-real-quartic",
            "sextic-6t10-quartic",
            "sextic-6t11-quartic",
            "sextic-6t11-real-quartic",
            "cubic-quartic",
            "s3-cubic-quartic",
            "mixed",
        ),
        default="sextic-quartic",
    )
    args = parser.parse_args()
    rows, report = generate_empirical_towers(
        attempts=args.attempts,
        limit=args.limit,
        seed=args.seed,
        family=args.family,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
