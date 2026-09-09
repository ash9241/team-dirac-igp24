#!/usr/bin/env sage
"""Independent A4-base wave for the exact 12T6 K4-star construction."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ, pari, prime_range


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "q6_a4c2_varied_a4_bases_20260727.jsonl"
SUMMARY = ROOT / "data" / "q6_a4c2_varied_a4_bases_20260727_summary.json"

module_spec = importlib.util.spec_from_file_location(
    "q6_main", ROOT / "q6_a4c2_k4_search_20260727.sage.py"
)
main_search = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(main_search)
shared = main_search.shared


BASES = [
    {
        "id": "D4225",
        "coefficients": [1, -3, 3, 2, 1],
        "shifts": [-3, 0, 1],
    },
    {
        "id": "D8281",
        "coefficients": [3, 4, 5, 1, 1],
        "shifts": [-1],
    },
    {
        "id": "D190096",
        "coefficients": [5, -4, 5, 2, 1],
        "shifts": [1, 2],
    },
    {
        "id": "D17689",
        "coefficients": [4, 7, 3, 0, 1],
        "shifts": [-1, 0],
    },
    {
        "id": "D210681",
        "coefficients": [4, -7, 6, 5, 1],
        "shifts": [-4, -3, 0, 1, 5],
    },
    {
        "id": "D200704",
        "coefficients": [4, 0, 8, 4, 1],
        "shifts": [-3, -1, 0],
    },
    {
        "id": "D23409",
        "coefficients": [8, 5, 6, 1, 1],
        "shifts": [-1],
    },
]


def atomic_text(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def base_data(specification):
    ring = PolynomialRing(QQ, "t")
    quartic = ring(specification["coefficients"])
    if quartic.galois_group().transitive_number() != 4:
        raise ArithmeticError(f"{specification['id']} is not A4")
    field = NumberField(quartic, f"a{specification['id']}")
    closure, embedding = field.galois_closure(
        names=f"omega{specification['id']}", map=True
    )
    roots = [root for root, multiplicity in quartic.change_ring(closure).roots()]
    w = (roots[0] - roots[1]) + 2 * (roots[2] - roots[3])
    minimal = w.minpoly()
    if minimal.degree() != 12 or any(
        minimal[index] != 0 for index in range(1, 12, 2)
    ):
        raise ArithmeticError(f"{specification['id']} anti-invariant failed")
    edge_field, edge_embedding = closure.subfield(w**2)
    return {
        "specification": specification,
        "ring": ring,
        "quartic": quartic,
        "closure": closure,
        "roots": roots,
        "wMinimal": minimal,
        "edgeField": edge_field,
        "edgeEmbedding": edge_embedding,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--d-values", default="2,3")
    parser.add_argument("--prime-limit", type=int, default=5000)
    args = parser.parse_args()
    d_values = [ZZ(value) for value in args.d_values.split(",") if value]
    h_specs = [(1, 0), (1, 1)]

    proof = main_search.target_star_proof()
    if proof["diagonalCosetActionLabel"] != "12T6" or not proof["exactK4StarIncidence"]:
        raise ArithmeticError("12T6 orientation proof failed")
    compatible, profiles = main_search.compatible_catalog()

    existing = []
    completed = set()
    if OUTPUT.exists():
        existing = [
            json.loads(line)
            for line in OUTPUT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        completed = {
            (
                row["baseId"],
                int(row["d"]),
                int(row["vertexShift"]),
                tuple(row["h"]),
            )
            for row in existing
        }

    candidate_ring = PolynomialRing(ZZ, "x")
    x = candidate_ring.gen()
    for specification in BASES:
        common = base_data(specification)
        for d in d_values:
            ring = common["ring"]
            variable = ring.gen()
            q = ring(
                sum(
                    common["wMinimal"][2 * index]
                    * d ** (6 - index)
                    * variable ** (2 * index)
                    for index in range(7)
                )
            )
            field = NumberField(q, f"b{specification['id']}_{d}")
            beta = field.gen()
            relative = field.relativize(
                beta**2, f"g{specification['id']}_{d}"
            )
            base = relative.base_field()
            z = base.gen()
            norm_data = base.pari_rnfnorm_data(relative)
            to_absolute = relative.structure()[0]
            generator = relative.gen()
            for vertex_shift in specification["shifts"]:
                value_at_shift = ZZ(common["quartic"](vertex_shift))
                if not value_at_shift.is_square():
                    raise ArithmeticError("listed vertex product is not square")
                edge_closure = (
                    (common["roots"][0] - vertex_shift)
                    * (common["roots"][1] - vertex_shift)
                )
                edge_value = common["edgeEmbedding"].preimage(edge_closure)
                base_edge_value = base(
                    sum(
                        edge_value[index] * (z / d) ** index
                        for index in range(len(edge_value.list()))
                    )
                )
                answer = norm_data.rnfisnorm(pari(base_edge_value))
                normable = str(answer[1]) == "1"
                for h_spec in h_specs:
                    key = (
                        specification["id"],
                        int(d),
                        vertex_shift,
                        h_spec,
                    )
                    if key in completed:
                        continue
                    if not normable:
                        row = {
                            "schemaVersion": "q6-a4c2-varied-a4-v1",
                            "baseId": specification["id"],
                            "quarticPolynomial": str(common["quartic"]),
                            "quarticDiscriminant": int(
                                common["quartic"].discriminant()
                            ),
                            "d": int(d),
                            "vertexShift": vertex_shift,
                            "h": list(h_spec),
                            "relativeNormable": False,
                            "status": "edge_value_not_relative_norm",
                            "networkCalls": 0,
                            "submissionCalls": 0,
                        }
                    else:
                        seed = relative(answer[0])
                        h0, h1 = h_spec
                        h = base(h0) + h1 * z
                        norm_one = (h + generator) / (h - generator)
                        if norm_one.relative_norm() != 1:
                            raise ArithmeticError("Hilbert-90 norm failed")
                        absolute = to_absolute(seed * norm_one)
                        scaled, square_scale, minimal = (
                            shared.integral_square_scale(absolute)
                        )
                        candidate = candidate_ring(minimal(x**2))
                        line = shared.canonical_line(candidate)
                        digest = hashlib.sha256(
                            line.encode("ascii")
                        ).hexdigest()
                        irreducible = bool(candidate.is_irreducible())
                        real_roots = (
                            int(candidate.number_of_real_roots())
                            if irreducible
                            else None
                        )
                        possible = set(compatible) if irreducible else set()
                        if real_roots is not None:
                            archimedean = tuple(
                                sorted(
                                    [1] * real_roots
                                    + [2] * ((24 - real_roots) // 2)
                                )
                            )
                            possible = {
                                target_t
                                for target_t in possible
                                if archimedean in profiles[target_t]
                            }
                        observations = []
                        for prime in prime_range(2, args.prime_limit + 1):
                            if len(possible) <= 1:
                                break
                            profile = shared.cycle_type(candidate, int(prime))
                            if profile is None:
                                continue
                            observations.append((int(prime), profile))
                            possible = {
                                target_t
                                for target_t in possible
                                if profile in profiles[target_t]
                            }
                        survivors = []
                        for target_t in sorted(possible):
                            item = compatible[target_t]
                            survivors.append(
                                {
                                    **item,
                                    "gate": main_search.live_gate(
                                        item["label"], real_roots, digest
                                    ),
                                }
                            )
                        row = {
                            "schemaVersion": "q6-a4c2-varied-a4-v1",
                            "baseId": specification["id"],
                            "quarticPolynomial": str(common["quartic"]),
                            "quarticDiscriminant": int(
                                common["quartic"].discriminant()
                            ),
                            "d": int(d),
                            "vertexShift": vertex_shift,
                            "vertexProductSquare": int(value_at_shift),
                            "h": list(h_spec),
                            "quotientPolynomial": str(q),
                            "quotientLabel": "12T6",
                            "relativeNormable": True,
                            "candidateCoefficientLine": line,
                            "candidateSha256": digest,
                            "candidateBytes": len(line.encode("ascii")),
                            "squareScale": square_scale,
                            "irreducible": irreducible,
                            "realRoots": real_roots,
                            "checkedGoodPrimes": len(observations),
                            "catalogSurvivors": survivors,
                            "status": (
                                "unique_current_tc0"
                                if len(survivors) == 1
                                and survivors[0]["gate"]["currentTc0"]
                                else "no_unique_current_tc0"
                            ),
                            "networkCalls": 0,
                            "submissionCalls": 0,
                        }
                    existing.append(row)
                    completed.add(key)
                    atomic_text(
                        OUTPUT,
                        "".join(
                            json.dumps(
                                item, separators=(",", ":"), sort_keys=True
                            )
                            + "\n"
                            for item in existing
                        ),
                    )
                    print(
                        json.dumps(
                            {
                                "base": specification["id"],
                                "d": int(d),
                                "t": vertex_shift,
                                "h": list(h_spec),
                                "norm": normable,
                                "r": row.get("realRoots"),
                                "survivors": [
                                    item["label"]
                                    for item in row.get(
                                        "catalogSurvivors", []
                                    )
                                ],
                                "tc0": [
                                    item["label"]
                                    for item in row.get(
                                        "catalogSurvivors", []
                                    )
                                    if item["gate"]["currentTc0"]
                                ],
                                "status": row["status"],
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )

    hits = [
        {
            "baseId": row["baseId"],
            "d": row["d"],
            "vertexShift": row["vertexShift"],
            "h": row["h"],
            "candidateSha256": row["candidateSha256"],
            "realRoots": row["realRoots"],
            "survivor": row["catalogSurvivors"][0],
        }
        for row in existing
        if row["status"] == "unique_current_tc0"
    ]
    summary = {
        "schemaVersion": "q6-a4c2-varied-a4-search-v1",
        "rows": len(existing),
        "baseIds": sorted({row["baseId"] for row in existing}),
        "normableRows": sum(row["relativeNormable"] for row in existing),
        "uniqueCurrentTc0": hits,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
