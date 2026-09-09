#!/usr/bin/env python3
"""Build norm-one Kummer lifts across the degree-12 LMFDB catalog.

For every totally real degree-12 field K having an automorphic degree-six
subfield L, this constructs h=w/sigma(w) in L and the absolute degree-24
field K(sqrt(h)).  The relative norm-one identity collapses the generic
six-dimensional Kummer orbit to a low-dimensional invariant module.  Factor
discriminants over K provide the intrinsic quadratic normal-closure twists.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

from routeA.constructions.compositum_8x3 import _run_gp, polynomial_expression
from routeA.ledger import candidate_hash, canonical_coefficients


DATA = Path(__file__).resolve().parent / "data"


def _load_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            label = str(row.get("label") or "")
            if label and label not in seen:
                seen.add(label)
                rows.append(row)
    return rows


def _w_expressions(options: int, seed: int) -> list[str]:
    # Small sparse elements give broad sign coverage without coefficient blowup.
    expressions: list[str] = []
    radius = max(2, (options + 1) // 2)
    for shift in range(-radius, radius + 1):
        expressions.append(f"u+({shift})")
    for a in (-3, -2, -1, 1, 2, 3):
        for b in (-2, -1, 1, 2):
            expressions.append(f"1+({a})*u+({b})*u^2")
    # Rotate deterministically so repeated arithmetic fields do not sample the
    # identical front of the sparse list.
    if expressions:
        offset = int(seed) % len(expressions)
        expressions = expressions[offset:] + expressions[:offset]
    return expressions[: max(1, int(options))]


def _build_field(
    field: dict[str, Any],
    *,
    options: int,
    linear_coset_radius: int,
    reduce_polynomial: bool,
    coefficient_limit: int,
    gp: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    counts: Counter[str] = Counter()
    base_t = int(field["base_t"])
    label = str(field.get("label") or f"12T{base_t}")
    coefficients = tuple(int(value) for value in field["coefficients"])
    if len(coefficients) != 13 or coefficients[-1] != 1:
        counts["invalid_base"] += 1
        return [], counts
    f = polynomial_expression(coefficients, "t")
    w_values = ",".join(_w_expressions(options, base_t + int(field.get("disc_abs") or 0)))
    # Both signs matter at the real places.  Wider translates t+c retain the
    # same generic orbit type while varying which real embeddings are positive.
    # This is especially useful when the server identifies the desired group
    # but the first primitive representative lands at the wrong signature.
    primitive_cosets = ["1", "-1"]
    for shift in range(-max(0, int(linear_coset_radius)), max(0, int(linear_coset_radius)) + 1):
        primitive_cosets.extend((f"t+({shift})", f"-t-({shift})"))
    primitive_cosets = list(dict.fromkeys(primitive_cosets))
    br_values = ",".join(primitive_cosets)
    reduction = "pr=polredabs(p)" if reduce_polynomial else "pr=polredbest(p)"
    # Keep every loop on one physical line because GP treats stdin newlines as
    # statement boundaries.
    script = (
        f"z='z;x='x;t='t;u='u;f={f};b=bnfinit(f);S=nfsubfields(f,6);"
        "FX=nffactor(b,subst(f,t,x));nd=0;d1=1;d2=1;"
        "for(fi=1,matsize(FX)[1],if(poldegree(FX[fi,1])>1,nd++;"
        "if(nd==1,d1=lift(poldisc(FX[fi,1])));if(nd==2,d2=lift(poldisc(FX[fi,1])))));"
        "TW=[1,d1,d2,d1*d2];"
        f"W=[{w_values}];for(si=1,#S,g=subst(S[si][1],t,u);emb=Mod(S[si][2],f);gc=nfgaloisconj(g);"
        "for(ci=1,#gc,if(lift(gc[ci])!=u,su=Mod(gc[ci],g);"
        "for(wi=1,#W,w=Mod(W[wi],g);ws=subst(lift(w),u,su);"
        f"if(ws!=0,h=w/ws;hk=Mod(subst(lift(h),u,emb),f);BR=[{br_values}];"
        "for(bi=1,#BR,for(ti=1,#TW,he=hk*Mod(BR[bi],f)*Mod(TW[ti],f);p=rnfequation(b,z^2-he);"
        f"if(poldegree(p)==24&&polisirreducible(p),{reduction};r=polsturm(pr);"
        "print(\"CAND|\",si,\"|\",wi,\"|\",bi,\"|\",ti,\"|\",r,\"|\",Vecrev(Vec(pr)))))))))));quit;"
    )
    try:
        output = _run_gp(script, gp=gp, timeout=timeout)
    except (RuntimeError, subprocess.TimeoutExpired):
        counts["gp_failure"] += 1
        return [], counts
    if "ERROR|no-automorphic-sextic" in output:
        counts["no_automorphic_sextic"] += 1
        return [], counts
    candidates: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.startswith("CAND|"):
            continue
        _, raw_subfield, raw_w, raw_base, raw_twist, raw_roots, raw_coefficients = line.split("|", 6)
        result = tuple(int(value) for value in ast.literal_eval(raw_coefficients))
        counts["locally_irreducible"] += 1
        if len(result) != 25 or result[-1] != 1:
            counts["invalid_degree"] += 1
            continue
        if max(abs(value) for value in result) >= int(coefficient_limit):
            counts["coefficient_limit"] += 1
            continue
        polynomial = canonical_coefficients(result)
        key = candidate_hash(polynomial)
        roots = int(raw_roots)
        candidates.append({
            "coefficients": polynomial,
            "candidate_hash": key,
            "local_root_count": roots,
            "local_irreducible": True,
            "target_t": 0,
            "target_r": roots,
            "label_probability": 0.0,
            "valid_probability": 1.0,
            "recipe_family": "catalog_relative_norm_one",
            "recipe_lineage": f"catalog-norm-one:12T{base_t}:{label}",
            "recipe_id": f"catalog-norm-one:{key[:20]}",
            "construction_overgroup": f"low_rank_Kummer_over_12T{base_t}",
            "parameters": {
                "base_t": base_t,
                "base_field_label": label,
                "subfield_index": int(raw_subfield),
                "w_index": int(raw_w),
                "primitive_coset_index": int(raw_base),
                "primitive_coset_expression": primitive_cosets[int(raw_base) - 1],
                "internal_twist_index": int(raw_twist),
                "relative_norm": 1,
            },
        })
    counts["emitted"] += len(candidates)
    return candidates, counts


def build_catalog(
    fields: Sequence[dict[str, Any]],
    *,
    bases: Sequence[int],
    source_signatures: Sequence[int],
    source_labels: Sequence[str],
    options_per_field: int,
    linear_coset_radius: int,
    reduce_polynomial: bool,
    limit_fields: int | None,
    limit_candidates: int,
    coefficient_limit: int,
    workers: int,
    gp: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    wanted = {int(value) for value in bases}
    wanted_signatures = {int(value) for value in source_signatures}
    wanted_labels = {str(value) for value in source_labels}
    eligible = [
        field for field in fields
        if 6 in {int(value) for value in field.get("subfield_degrees", [])}
        and (not wanted or int(field["base_t"]) in wanted)
        and (not wanted_labels or str(field.get("label") or "") in wanted_labels)
        and (
            not wanted_signatures
            or int(field.get("source_signature", -1)) in wanted_signatures
        )
    ]
    eligible.sort(key=lambda row: (int(row["base_t"]), int(row.get("disc_abs") or 0)))
    if limit_fields is not None:
        eligible = eligible[: max(0, int(limit_fields))]
    counts: Counter[str] = Counter(fields=len(fields), eligible_fields=len(eligible))
    unique: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        futures = [
            executor.submit(
                _build_field,
                field,
                options=options_per_field,
                linear_coset_radius=linear_coset_radius,
                reduce_polynomial=reduce_polynomial,
                coefficient_limit=coefficient_limit,
                gp=gp,
                timeout=timeout,
            )
            for field in eligible
        ]
        for future in as_completed(futures):
            rows, local_counts = future.result()
            counts.update(local_counts)
            for row in rows:
                unique.setdefault(str(row["candidate_hash"]), row)
    rows = sorted(
        unique.values(),
        key=lambda row: (
            int((row.get("parameters") or {}).get("base_t", 0)),
            int(row["local_root_count"]),
            str(row["candidate_hash"]),
        ),
    )[: max(1, int(limit_candidates))]
    counts["unique"] = len(unique)
    counts["selected"] = len(rows)
    return rows, dict(sorted(counts.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--catalog",
        action="append",
        type=Path,
        default=[],
        help="JSONL catalog; defaults to primary plus alternates",
    )
    parser.add_argument("--options-per-field", type=int, default=4)
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--source-signature", action="append", type=int, default=[])
    parser.add_argument("--source-label", action="append", default=[])
    parser.add_argument("--linear-coset-radius", type=int, default=1)
    parser.add_argument(
        "--skip-polredabs",
        action="store_true",
        help="use fast polredbest instead of canonical-but-expensive polredabs",
    )
    parser.add_argument("--limit-fields", type=int)
    parser.add_argument("--limit-candidates", type=int, default=1000)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--gp", default="gp")
    parser.add_argument("--timeout", type=float, default=1200)
    args = parser.parse_args()
    catalogs = args.catalog or [
        DATA / "degree12_lmfdb_catalog.jsonl",
        DATA / "degree12_lmfdb_alternates.jsonl",
    ]
    rows, counts = build_catalog(
        _load_jsonl(catalogs),
        bases=args.base,
        source_signatures=args.source_signature,
        source_labels=args.source_label,
        options_per_field=max(1, args.options_per_field),
        linear_coset_radius=max(0, args.linear_coset_radius),
        reduce_polynomial=not args.skip_polredabs,
        limit_fields=args.limit_fields,
        limit_candidates=max(1, args.limit_candidates),
        coefficient_limit=args.coefficient_limit,
        workers=max(1, args.workers),
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
