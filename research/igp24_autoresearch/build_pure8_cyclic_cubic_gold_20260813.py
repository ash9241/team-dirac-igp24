#!/usr/bin/env python3
"""Generate cyclic-cubic norm forms y^8-A for the order-768 gold frontier."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import subprocess
from pathlib import Path


PRELUDE = r"""
minval3(expr, f) = my(m=10^30); foreach(polroots(f), rt, m=min(m, real(subst(expr, x, rt)))); m;
spos3(u) = my(m=minval3(u, f)); if(m<=0, u+(1+ceil(-m)), u);
"""


def canonical(values: list[int]) -> str:
    return ",".join(map(str, values))


def jobs(*, shard: int, shards: int, coefficient_bound: int, t_min: int, t_max: int):
    index = 0
    # Interleave cubic parameters inside the coefficient loop.  With ``t`` as
    # the outer loop, a high-yield chamber could fill every output shard before
    # the search ever advanced beyond the first cyclic cubic.
    t_values = [t for t in range(t_min, t_max + 1) if t != -1]
    for a0, a1, a2 in itertools.product(range(-coefficient_bound, coefficient_bound + 1), repeat=3):
        if (a0, a1, a2) == (0, 0, 0):
            continue
        for t in t_values:
            if index % shards == shard:
                yield t, a0, a1, a2
            index += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=2)
    parser.add_argument("--coefficient-bound", type=int, default=8)
    parser.add_argument("--t-min", type=int, default=-24)
    parser.add_argument("--t-max", type=int, default=48)
    parser.add_argument("--mode", choices=("negative", "positive", "mixed"), default="negative")
    parser.add_argument("--gp", default="gp")
    args = parser.parse_args()
    if not 0 <= args.shard < args.shards:
        raise ValueError("shard must satisfy 0 <= shard < shards")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")

    selected_jobs = list(jobs(
        shard=args.shard,
        shards=args.shards,
        coefficient_bound=args.coefficient_bound,
        t_min=args.t_min,
        t_max=args.t_max,
    ))
    # PARI processes one expression per line.  Stop after a generous attempt
    # cap so output parsing stays bounded even when a chamber has low yield.
    attempt_cap = min(len(selected_jobs), max(args.limit * 30, 20000))
    scripts = []
    meta = []
    for index, (t, a0, a1, a2) in enumerate(selected_jobs[:attempt_cap]):
        raw = f"(({a0})+({a1})*x+({a2})*x^2)"
        if args.mode == "negative":
            radicand = f"(-spos3({raw}))"
        elif args.mode == "positive":
            radicand = f"spos3({raw})"
        else:
            radicand = raw
        scripts.append(
            f"f=x^3-({t})*x^2-({t}+3)*x-1;"
            f"AA={radicand};"
            "p=subst(polresultant(f,y^8-AA,x),y,x);"
            f"if(poldegree(p)==24&&polcoef(p,24)==1&&polisirreducible(p),print(\"RA|{index}|\",polsturm(p),\"|\",Vec(p)),print(\"RA|{index}|X|X\"));"
        )
        meta.append({"t": t, "a": [a0, a1, a2], "mode": args.mode})
    payload = PRELUDE + "\n".join(scripts) + "\nquit;\n"
    result = subprocess.run(
        [args.gp, "-q", "-f", "-s", "800000000"],
        input=payload,
        text=True,
        capture_output=True,
        timeout=3600,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or "PARI generation failed")

    rows = []
    seen = set()
    root_counts: dict[int, int] = {}
    for raw_line in result.stdout.splitlines():
        if not raw_line.startswith("RA|"):
            continue
        _tag, raw_index, raw_roots, raw_vector = raw_line.split("|", 3)
        if raw_roots == "X":
            continue
        values = list(reversed([int(item.strip()) for item in raw_vector.strip()[1:-1].split(",")]))
        if len(values) != 25 or values[-1] != 1 or not values[0]:
            continue
        line = canonical(values)
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        roots = int(raw_roots)
        root_counts[roots] = root_counts.get(roots, 0) + 1
        rows.append({
            "candidate_hash": digest,
            "coefficients": line,
            "construction_overgroup": "pure-octic-radical-over-cyclic-cubic",
            "irreducible": True,
            "local_irreducible": True,
            "local_root_count": roots,
            "parameters": meta[int(raw_index)],
            "primitiveMonicDegree24": True,
            "recipe_family": "PURE8C3_GOLD",
            "target_r": roots,
            "target_t": 0,
        })
        if len(rows) >= args.limit:
            break
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    report = {
        "attempted": attempt_cap,
        "coefficientBound": args.coefficient_bound,
        "generated": len(rows),
        "mode": args.mode,
        "rootDistribution": dict(sorted(root_counts.items())),
        "shard": args.shard,
        "shards": args.shards,
        "tRange": [args.t_min, args.t_max],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if len(rows) < args.limit:
        raise ValueError(f"generated only {len(rows)} of requested {args.limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
