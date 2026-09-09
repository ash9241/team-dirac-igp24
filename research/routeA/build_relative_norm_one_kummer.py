#!/usr/bin/env python3
"""Build low-rank Kummer lifts above the degree-12 field 12T61.

The selected 12T61 field contains an A4 degree-six subfield ``L`` and a
cyclic cubic subfield ``F``.  If ``sigma`` is the non-trivial automorphism of
``L/F``, Hilbert 90 elements

    h = w / sigma(w)

have relative norm one.  The six conjugate squareclasses of ``h`` therefore
come in three inverse pairs, forcing the Kummer kernel to have rank at most
three instead of the generic rank six.  This is the exact kernel dimension of
the rare order-768 groups above 12T61.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from routeA.constructions.compositum_8x3 import _run_gp
from routeA.ledger import candidate_hash, canonical_coefficients


# The LMFDB 12T61 source is f(t) = g(t^2).  The cubic subfield generator e
# below is PARI's exact nfsubfields(g, 3) embedding.  In L/F the other root is
# sigma(u) = e/5 + 6 - u.
BASE_FIELDS = {
    1: (
        "12.12.447160536237867008000.1",
        "u^6-35*u^5+421*u^4-2236*u^3+5160*u^2-3900*u+500",
    ),
    2: (
        "12.12.1774333007791856287744.2",
        "u^6-43*u^5+637*u^4-4342*u^3+14198*u^2-19840*u+7936",
    ),
    3: (
        "12.12.7097332031167425150976.2",
        "u^6-46*u^5+478*u^4-2140*u^3+4712*u^2-4960*u+1984",
    ),
}


def _poly_expression(coefficients: Sequence[int]) -> str:
    terms: list[str] = []
    for exponent, coefficient in enumerate(coefficients):
        value = int(coefficient)
        if not value:
            continue
        if exponent == 0:
            terms.append(str(value))
        elif exponent == 1:
            terms.append(f"({value})*u")
        else:
            terms.append(f"({value})*u^{exponent}")
    return "+".join(terms) or "0"


def parameter_vectors(attempts: int, seed: int) -> list[tuple[int, ...]]:
    """Return deterministic sparse and dense representatives for w in L."""

    values: list[tuple[int, ...]] = []
    seen: set[tuple[int, ...]] = set()

    def add(raw: Iterable[int]) -> None:
        vector = tuple(int(value) for value in raw)
        if not any(vector) or vector in seen:
            return
        # w and -w define the same Hilbert-90 element.
        first = next(value for value in vector if value)
        if first < 0:
            vector = tuple(-value for value in vector)
        if vector not in seen:
            seen.add(vector)
            values.append(vector)

    # Shift families expose special norm and ramification relations cleanly.
    radius = max(12, min(120, attempts // 12))
    for exponent in range(1, 6):
        for constant in range(-radius, radius + 1):
            vector = [0] * 6
            vector[0] = constant
            vector[exponent] = 1
            add(vector)

    # Small sparse projective points find relations missed by one-parameter
    # shifts while keeping the reduced absolute polynomials compact.
    scalars = (-3, -2, -1, 1, 2, 3)
    for left in range(1, 6):
        for right in range(left + 1, 6):
            for a in scalars:
                for b in scalars:
                    vector = [0] * 6
                    vector[0] = 1
                    vector[left] = a
                    vector[right] = b
                    add(vector)

    rng = random.Random(int(seed))
    while len(values) < max(attempts * 3, attempts + 500):
        height = rng.choice((2, 3, 4, 5, 7))
        vector = [rng.randint(-height, height) for _ in range(6)]
        if sum(value != 0 for value in vector) < 3:
            continue
        add(vector)
    return values[: max(attempts * 3, attempts + 500)]


def _build_chunk(
    vectors: Sequence[tuple[int, ...]],
    *,
    include_special: bool,
    base_index: int,
    gp: str,
    timeout: float,
) -> list[tuple[str, int, int, int, tuple[int, ...]]]:
    expressions = ",".join(_poly_expression(vector) for vector in vectors)
    _, g = BASE_FIELDS[int(base_index)]
    radical_bases = (
        "BR=[hn,Mod(6-u,g)*hn,Mod(5-u,g)*hn];"
        if int(base_index) == 1
        else "BR=[hn];"
    )
    # GP's stdin parser treats a newline as a statement boundary, so keep the
    # loop body on one physical line.
    script = (
        f"z='z;x='x;t='t;u='u;g={g};gc=nfgaloisconj(g);su=Mod(gc[2],g);"
        f"f=subst(g,u,t^2);b=bnfinit(f);W=[{expressions}];"
        "FX=nffactor(b,subst(f,t,x));d2=lift(poldisc(FX[3,1]));"
        "d4a=lift(poldisc(FX[4,1]));d4b=lift(poldisc(FX[5,1]));"
        "TW=[1,d2,d4a,d2*d4a];"
        "for(i=1,#W,w=Mod(W[i],g);ws=subst(lift(w),u,su);"
        f"if(ws!=0,hn=w/ws;{radical_bases}"
        "for(k=1,#BR,for(j=1,#TW,h=BR[k];he=subst(lift(h),u,t^2)*TW[j];"
        "p=rnfequation(b,z^2-Mod(he,f));"
        "if(poldegree(p)==24&&polisirreducible(p),pr=polredabs(p);"
        "r=polsturm(pr);print(\"CAND|\",k,\"|\",i,\"|\",j,\"|\",r,\"|\","
        "Vecrev(Vec(pr))))))));"
        + "quit;"
    )
    output = _run_gp(script, gp=gp, timeout=timeout)
    rows: list[tuple[str, int, int, int, tuple[int, ...]]] = []
    for line in output.splitlines():
        if not line.startswith("CAND|"):
            continue
        _, raw_kind, raw_index, raw_twist, raw_roots, raw_coefficients = line.split("|", 5)
        kind = ("normone", "norm_e", "norm_5")[int(raw_kind) - 1]
        coefficients = tuple(int(value) for value in ast.literal_eval(raw_coefficients))
        rows.append((
            kind,
            int(raw_index) - 1,
            int(raw_twist) - 1,
            int(raw_roots),
            coefficients,
        ))
    return rows


def build_candidates(
    *,
    attempts: int,
    seed: int,
    base_index: int,
    per_signature: int,
    coefficient_limit: int,
    chunk_size: int,
    gp: str,
    timeout: float,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    vectors = parameter_vectors(attempts, seed)
    by_signature: dict[int, list[dict[str, object]]] = defaultdict(list)
    unique: set[str] = set()
    counts: Counter[str] = Counter()
    for offset in range(0, min(attempts, len(vectors)), chunk_size):
        chunk = vectors[offset : offset + chunk_size]
        for kind, local_index, twist_index, roots, coefficients in _build_chunk(
            chunk,
            include_special=(offset == 0),
            base_index=base_index,
            gp=gp,
            timeout=timeout,
        ):
            counts["locally_irreducible"] += 1
            if len(coefficients) != 25 or coefficients[-1] != 1:
                counts["invalid_degree"] += 1
                continue
            if max(abs(value) for value in coefficients) >= coefficient_limit:
                counts["coefficient_limit"] += 1
                continue
            line = canonical_coefficients(coefficients)
            key = candidate_hash(line)
            if key in unique:
                counts["duplicates"] += 1
                continue
            unique.add(key)
            vector = chunk[local_index]
            twist_names = (
                "1", "disc2", "disc4a", "disc2*disc4a",
            )
            by_signature[roots].append({
                "coefficients": line,
                "candidate_hash": key,
                "local_root_count": roots,
                "local_irreducible": True,
                "target_t": 0,
                "target_r": roots,
                "label_probability": 0.0,
                "valid_probability": 1.0,
                "recipe_family": (
                    "relative_norm_one_kummer_12t61"
                    if kind == "normone"
                    else "tetrahedral_rank3_kummer_12t61"
                ),
                "recipe_lineage": f"internal-kummer-twist:12T61:{kind}",
                "recipe_id": f"internal-kummer-twist:{key[:20]}",
                "construction_overgroup": "rank3_Kummer_over_12T61",
                "parameters": {
                    "base_t": 61,
                    "base_field_index": int(base_index),
                    "base_field_label": BASE_FIELDS[int(base_index)][0],
                    "subfield_t": 4,
                    "relative_base_t": 1,
                    "w_coefficients": list(vector),
                    "base_radical": {
                        "normone": "w/sigma(w)",
                        "norm_e": "(6-u)*w/sigma(w)",
                        "norm_5": "(5-u)*w/sigma(w)",
                    }[kind],
                    "relative_norm_class": {
                        "normone": "1",
                        "norm_e": "-e/5",
                        "norm_5": "-5",
                    }[kind],
                    "internal_twist": twist_names[twist_index],
                },
            })
    rows: list[dict[str, object]] = []
    roots_order = (8, 16, 12, 4, 20, 0, 24)
    for roots in roots_order:
        rows.extend(by_signature.get(roots, [])[:per_signature])
    # Fill any unused capacity from all signatures deterministically.
    selected = {str(row["candidate_hash"]) for row in rows}
    for roots in sorted(by_signature):
        for row in by_signature[roots]:
            if len(rows) >= per_signature * max(1, len(by_signature)):
                break
            if str(row["candidate_hash"]) not in selected:
                selected.add(str(row["candidate_hash"]))
                rows.append(row)
    counts["selected"] = len(rows)
    for roots, options in by_signature.items():
        counts[f"signature_{roots}"] = len(options)
    return rows, dict(sorted(counts.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--attempts", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=1500)
    parser.add_argument("--base-field", type=int, choices=sorted(BASE_FIELDS), default=1)
    parser.add_argument("--per-signature", type=int, default=250)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--gp", default="gp")
    parser.add_argument("--timeout", type=float, default=1200)
    args = parser.parse_args()
    rows, counts = build_candidates(
        attempts=max(1, args.attempts),
        seed=args.seed,
        base_index=args.base_field,
        per_signature=max(1, args.per_signature),
        coefficient_limit=args.coefficient_limit,
        chunk_size=max(1, args.chunk_size),
        gp=args.gp,
        timeout=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
