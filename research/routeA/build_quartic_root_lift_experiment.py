#!/usr/bin/env python3
"""Build small-orbit quadratic lifts of D4 quartics over cyclic cubics."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from routeA.empirical_recipes import ES, el3, rnd3, run_gp
from routeA.ledger import candidate_hash, canonical_coefficients


def _job(index: int) -> tuple[dict, str]:
    t0 = random.choice([value for value in range(-8, 19) if value != -1])
    e = random.choice(ES)
    a, b, g = el3(rnd3()), el3(rnd3()), el3(rnd3())
    c = random.choice((-3, -2, -1, 1, 2, 3))
    d = random.choice((-3, -2, -1, 1, 2, 3))
    form = random.choice(("d4", "d4", "d4", "c4", "pure4"))
    lift = random.choice((
        "z", "z+c", "z2+c", "z2+cz+d", "z(z+c)", "z3+c", "z3+cz+d",
    ))
    positive_scale = random.random() < 0.45
    g_expression = f"spos3({g})" if positive_scale else g
    if form == "c4":
        quartic = (
            f"AA={a};BB={b};gg={g_expression};DD=AA^2+BB^2;"
            "Q4=z^4-2*gg*DD*z^2+gg^2*DD*BB^2;"
        )
    elif form == "pure4":
        quartic = f"Q4=z^4-({a});"
    else:
        quartic = (
            f"AA={a};BB={b};gg={g_expression};DD=AA^2+({e})*BB^2;"
            f"Q4=z^4-2*gg*DD*z^2+gg^2*({e})*DD*BB^2;"
        )
    h = {
        "z": "z",
        "z+c": f"z+({c})",
        "z2+c": f"z^2+({c})",
        "z2+cz+d": f"z^2+({c})*z+({d})",
        "z(z+c)": f"z*(z+({c}))",
        "z3+c": f"z^3+({c})",
        "z3+cz+d": f"z^3+({c})*z+({d})",
    }[lift]
    dial = {
        "fam": "QRL", "t": t0, "e": e, "form": form, "lift": lift,
        "c": c, "d": d, "A": a, "B": b, "g": g, "pg": positive_scale,
    }
    script = (
        f"f=x^3-({t0})*x^2-({t0}+3)*x-1;"
        f"if(polisirreducible(f),{quartic}"
        f"P8=polresultant(Q4,y^2-({h}),z);"
        "p=subst(polresultant(f,P8,x),y,x);"
        "if(poldegree(p)==24&&polcoef(p,24)==1&&polisirreducible(p),"
        f"print(\"RA|{index}|\",polsturm(p),\"|\",Vec(p)),"
        f"print(\"RA|{index}|X|X\")),print(\"RA|{index}|X|X\"));"
    )
    return dial, script


def generate(*, attempts: int, limit: int, seed: int) -> tuple[list[dict], dict]:
    random.seed(seed)
    jobs: list[dict] = []
    scripts: list[str] = []
    for index in range(attempts):
        dial, script = _job(index)
        jobs.append(dial)
        scripts.append(script)
    result = run_gp("\n".join(scripts))
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or "PARI generation failed")
    rows: list[dict] = []
    seen: set[str] = set()
    rejected_height = 0
    for line in result.stdout.splitlines():
        if not line.startswith("RA|"):
            continue
        _, raw_index, raw_roots, raw_vector = line.split("|", 3)
        if raw_roots == "X":
            continue
        coefficients = list(reversed([
            int(part.strip()) for part in raw_vector.strip()[1:-1].split(",")
        ]))
        if len(coefficients) != 25 or coefficients[-1] != 1 or not coefficients[0]:
            continue
        if max(map(abs, coefficients)) >= 10**55:
            rejected_height += 1
            continue
        serialized = canonical_coefficients(coefficients)
        key = candidate_hash(serialized)
        if key in seen:
            continue
        seen.add(key)
        roots = int(raw_roots)
        rows.append({
            "candidate_hash": key,
            "coefficients": serialized,
            "construction_overgroup": "quadratic-lift-of-quartic-over-cyclic-cubic",
            "local_irreducible": True,
            "local_root_count": roots,
            "parameters": jobs[int(raw_index)],
            "recipe_family": "QRL",
            "source_host": "credential-free-quartic-root-lift",
            "target_r": roots,
            "target_t": 0,
        })
        if len(rows) >= limit:
            break
    return rows, {
        "attempts": attempts,
        "generated": len(rows),
        "rejected_height": rejected_height,
        "seed": seed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--attempts", type=int, default=6000)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1500061)
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
