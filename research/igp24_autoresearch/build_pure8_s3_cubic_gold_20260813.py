#!/usr/bin/env python3
"""Generate S3-cubic norm forms y^8-A for the order-1536 gold frontier."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import subprocess
from itertools import islice
from pathlib import Path


PRELUDE = r"""
minval3(expr, f) = my(m=10^30); foreach(polroots(f), rt, m=min(m, real(subst(expr, x, rt)))); m;
spos3(u) = my(m=minval3(u, f)); if(m<=0, u+(1+ceil(-m)), u);
"""


def canonical(values: list[int]) -> str:
    return ",".join(map(str, values))


def cubic_parameters(p_bound: int, q_bound: int) -> list[tuple[int, int]]:
    """Totally real depressed cubics x^3+p*x+q with nonsquare discriminant."""
    result = []
    for p in range(-p_bound, 0):
        for q in range(-q_bound, q_bound + 1):
            disc = -4 * p**3 - 27 * q**2
            if disc <= 0 or math.isqrt(disc) ** 2 == disc:
                continue
            # A rational root must divide q; PARI still certifies the final
            # degree-24 polynomial, but filtering obvious reducible cubics here
            # keeps the attempt budget useful.
            if q == 0:
                continue
            if any(q % d == 0 and d**3 + p * d + q == 0 for d in range(-abs(q), abs(q) + 1) if d):
                continue
            result.append((p, q))
    if not result:
        raise ValueError("no totally real S3 cubic parameters in requested bounds")
    return result


def jobs(*, shard: int, shards: int, coefficient_bound: int, p_bound: int, q_bound: int):
    index = 0
    cubics = cubic_parameters(p_bound, q_bound)
    for a0, a1, a2 in itertools.product(range(-coefficient_bound, coefficient_bound + 1), repeat=3):
        if (a0, a1, a2) == (0, 0, 0):
            continue
        for p, q in cubics:
            if index % shards == shard:
                yield p, q, a0, a1, a2
            index += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=16)
    parser.add_argument("--coefficient-bound", type=int, default=10)
    parser.add_argument("--p-bound", type=int, default=40)
    parser.add_argument("--q-bound", type=int, default=30)
    parser.add_argument("--gp", default="gp")
    args = parser.parse_args()
    if not 0 <= args.shard < args.shards:
        raise ValueError("shard must satisfy 0 <= shard < shards")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")

    attempt_cap = max(args.limit * 30, 20000)
    selected_jobs = list(islice(jobs(
        shard=args.shard,
        shards=args.shards,
        coefficient_bound=args.coefficient_bound,
        p_bound=args.p_bound,
        q_bound=args.q_bound,
    ), attempt_cap))
    attempt_cap = len(selected_jobs)
    scripts = []
    meta = []
    for index, (p, q, a0, a1, a2) in enumerate(selected_jobs[:attempt_cap]):
        raw = f"(({a0})+({a1})*x+({a2})*x^2)"
        scripts.append(
            f"f=x^3+({p})*x+({q});"
            f"AA=(-spos3({raw}));"
            "P=subst(polresultant(f,y^8-AA,x),y,x);"
            f"if(poldegree(P)==24&&polcoef(P,24)==1&&polisirreducible(P),print(\"RA|{index}|\",polsturm(P),\"|\",Vec(P)),print(\"RA|{index}|X|X\"));"
        )
        meta.append({"p": p, "q": q, "a": [a0, a1, a2]})
    result = subprocess.run(
        [args.gp, "-q", "-f", "-s", "800000000"],
        input=PRELUDE + "\n".join(scripts) + "\nquit;\n",
        text=True,
        capture_output=True,
        timeout=3600,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or "PARI generation failed")

    rows = []
    seen = set()
    roots = {}
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
        root_count = int(raw_roots)
        roots[root_count] = roots.get(root_count, 0) + 1
        rows.append({
            "candidate_hash": digest,
            "coefficients": line,
            "construction_overgroup": "pure-octic-radical-over-s3-cubic",
            "irreducible": True,
            "local_irreducible": True,
            "local_root_count": root_count,
            "parameters": meta[int(raw_index)],
            "primitiveMonicDegree24": True,
            "recipe_family": "PURE8S3_GOLD",
            "target_r": root_count,
            "target_t": 0,
        })
        if len(rows) >= args.limit:
            break
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    report = {
        "attempted": attempt_cap,
        "cubicCount": len(cubic_parameters(args.p_bound, args.q_bound)),
        "generated": len(rows),
        "rootDistribution": dict(sorted(roots.items())),
        "shard": args.shard,
        "shards": args.shards,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if len(rows) < args.limit:
        raise ValueError(f"generated only {len(rows)} of requested {args.limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
