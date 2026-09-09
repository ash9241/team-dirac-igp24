#!/usr/bin/env python3
"""Generate r=20 cyclic-cubic/D4 candidates with a mixed sextic scale.

The historical CT/EDOC construction kept the D4 scale in the cubic base, so
the two embeddings above each real cubic place had the same sign and the
degree-24 signature was restricted to multiples of eight.  Here the scale is
``a + z`` in K3(z), z^2=w.  Requiring five of its six real conjugates to be
positive gives exactly twenty real roots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
from pathlib import Path

from routeA.ledger import candidate_hash
from routeA.oracle import GP


def _element(rng: random.Random, bound: int) -> str:
    values = [rng.randint(-bound, bound) for _ in range(3)]
    if values == [0, 0, 0]:
        values[0] = 1
    return f"({values[0]}+({values[1]})*x+({values[2]})*x^2)"


def generate(
    *, attempts: int, limit: int, seed: int, coefficient_bound: int = 2
) -> list[dict]:
    rng = random.Random(seed)
    jobs = []
    statements = []
    for index in range(attempts):
        h = _element(rng, coefficient_bound)
        scale_base = _element(rng, coefficient_bound + 1)
        a0, a1 = _element(rng, coefficient_bound), _element(rng, coefficient_bound)
        b0, b1 = _element(rng, coefficient_bound), _element(rng, coefficient_bound)
        jobs.append({
            "h": h,
            "scale_base": scale_base,
            "A0": a0,
            "A1": a1,
            "B0": b0,
            "B1": b1,
        })
        statements.append(
            f"f=x^3+5*x^2+2*x-1; "
            f"hh={h}; ww=lift(Mod(hh^2+1,f)); aa={scale_base}; gg=aa+z; "
            f"if(pos6(ww,gg,f)==5, "
            f"AA=({a0})+({a1})*z; BB=({b0})+({b1})*z; DD=AA^2+11*BB^2; "
            f"R4=polresultant(z^2-ww,y^2-DD,z); "
            f"p12=subst(polresultant(f,R4,x),y,x); "
            f"p6=subst(polresultant(f,z^2-ww,x),z,x); "
            f"Q4=y^4-2*gg*DD*y^2+11*gg^2*DD*BB^2; "
            f"P8=polresultant(z^2-ww,Q4,z); p=subst(polresultant(f,P8,x),y,x); "
            f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p6) && "
            f"polisirreducible(p12) && polgalois(p6)[3]==6 && "
            f"polisirreducible(p) && polsturm(p)==20, "
            f"print(\"MG|{index}|\",Vec(p),\"|\",Vec(p6),\"|\",Vec(p12),\"|\",abs(poldisc(p)))));"
        )
    prelude = r"""
default(new_galois_format,1);
pos6(w,g,f)=my(rr=polroots(f),n=0,wi,gi); for(i=1,#rr,wi=real(subst(w,x,rr[i])); if(wi<=0,return(-1)); forstep(s=-1,1,2,gi=real(subst(subst(g,x,rr[i]),z,s*sqrt(wi))); if(gi>0,n++))); n;
"""
    result = subprocess.run(
        [GP, "-q", "-f", "-s", "400000000"],
        input=prelude + "\n".join(statements) + "\nquit;\n",
        capture_output=True,
        text=True,
        timeout=1800,
    )
    rows = []
    for raw in result.stdout.splitlines():
        if not raw.startswith("MG|"):
            continue
        _, raw_index, raw_p, raw_p6, raw_p12, raw_disc = raw.split("|", 5)
        vectors = []
        for raw_vector in (raw_p, raw_p6, raw_p12):
            values = [int(value) for value in raw_vector[1:-1].split(",")]
            vectors.append(list(reversed(values)))
        coefficients, degree6, degree12 = vectors
        line = ",".join(map(str, coefficients))
        metadata = jobs[int(raw_index)]
        rows.append({
            "candidate_hash": candidate_hash(line),
            "coefficients": line,
            "compatible_labels": list(range(18986, 18998)),
            "construction_overgroup": "mixed-scale-relative-D4-over-6T6",
            "intermediate_degree6_coefficients": degree6,
            "intermediate_degree12_coefficients": degree12,
            "local_irreducible": True,
            "local_root_count": 20,
            "polynomial_discriminant_abs": int(raw_disc),
            "recipe_family": "mixed_scale_d4_ct_tminus5_e11",
            "recipe_id": "mixed-scale-d4:" + hashlib.sha256(
                json.dumps(metadata, sort_keys=True).encode()
            ).hexdigest()[:20],
            "recipe_lineage": "mixed-scale-d4:t-5:e11",
            "parameters": metadata,
            "quotient_evidence": {
                "expected_quotient3": 1,
                "expected_quotient6": 6,
                "expected_quotient12": 222,
            },
        })
        if len(rows) >= limit:
            break
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--attempts", type=int, default=3000)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1898620)
    parser.add_argument("--coefficient-bound", type=int, default=2)
    args = parser.parse_args()
    rows = generate(
        attempts=args.attempts,
        limit=args.limit,
        seed=args.seed,
        coefficient_bound=args.coefficient_bound,
    )
    Path(args.output).write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )
    print(json.dumps({"attempts": args.attempts, "generated": len(rows)}))


if __name__ == "__main__":
    main()
