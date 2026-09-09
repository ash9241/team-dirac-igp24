#!/usr/bin/env sage
"""Vary A4xC2 quadratic bases, vertex square shifts, and M-valued H90 twists."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ, pari, prime_range


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "q6_a4c2_multistratum_pilot_20260727.jsonl"
SUMMARY = ROOT / "data" / "q6_a4c2_multistratum_pilot_20260727_summary.json"

module_spec = importlib.util.spec_from_file_location(
    "q6_main", ROOT / "q6_a4c2_k4_search_20260727.sage.py"
)
main_search = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(main_search)
shared = main_search.shared


def atomic_text(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def common_a4_data():
    ring = PolynomialRing(QQ, "t")
    t = ring.gen()
    quartic = t**4 - 3 * t**3 + 3 * t**2 + 2 * t + 1
    quartic_field = NumberField(quartic, "a")
    closure, embedding = quartic_field.galois_closure(names="omega", map=True)
    roots = [root for root, multiplicity in quartic.change_ring(closure).roots()]
    w = (roots[0] - roots[1]) + 2 * (roots[2] - roots[3])
    w_minimal = w.minpoly()
    edge_field, edge_embedding = closure.subfield(w**2)
    return {
        "ring": ring,
        "quartic": quartic,
        "closure": closure,
        "roots": roots,
        "w": w,
        "wMinimal": w_minimal,
        "edgeField": edge_field,
        "edgeEmbedding": edge_embedding,
    }


def tower_for_d(common, d):
    ring = common["ring"]
    x = ring.gen()
    minimal = common["wMinimal"]
    q = ring(
        sum(
            minimal[2 * index] * d ** (6 - index) * x ** (2 * index)
            for index in range(7)
        )
    )
    field = NumberField(q, f"beta{d}")
    beta = field.gen()
    relative = field.relativize(beta**2, f"gamma{d}")
    return {
        "d": int(d),
        "q": q,
        "field": field,
        "relative": relative,
        "relativeToAbsolute": relative.structure()[0],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--d-values", default="2,3,5,6,7,10,11,13")
    parser.add_argument("--prime-limit", type=int, default=5000)
    args = parser.parse_args()
    d_values = [ZZ(value) for value in args.d_values.split(",") if value]
    vertex_shifts = [-2, 0, 1, 2, 4]
    h_specs = [
        (1, 0),
        (2, 0),
        (1, 1),
        (1, -1),
        (2, 1),
        (2, -1),
        (1, 2),
        (1, -2),
    ]

    proof = main_search.target_star_proof()
    if proof["diagonalCosetActionLabel"] != "12T6" or not proof["exactK4StarIncidence"]:
        raise ArithmeticError("12T6 orientation proof failed")
    compatible, profiles = main_search.compatible_catalog()
    common = common_a4_data()
    existing = []
    completed = set()
    if OUTPUT.exists():
        existing = [
            json.loads(line)
            for line in OUTPUT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        completed = {
            (int(row["d"]), int(row["vertexShift"]), tuple(row["h"]))
            for row in existing
        }

    candidate_ring = PolynomialRing(ZZ, "x")
    x = candidate_ring.gen()
    for d in d_values:
        tower = tower_for_d(common, d)
        relative = tower["relative"]
        base = relative.base_field()
        z = base.gen()
        norm_data = base.pari_rnfnorm_data(relative)
        generator = relative.gen()
        for vertex_shift in vertex_shifts:
            if not ZZ(common["quartic"](vertex_shift)).is_square():
                raise ArithmeticError("vertex shift lost rational-square product")
            edge_value_in_closure = (
                (common["roots"][0] - vertex_shift)
                * (common["roots"][1] - vertex_shift)
            )
            edge_value = common["edgeEmbedding"].preimage(edge_value_in_closure)
            base_edge_value = base(
                sum(
                    edge_value[index] * (z / d) ** index
                    for index in range(len(edge_value.list()))
                )
            )
            answer = norm_data.rnfisnorm(pari(base_edge_value))
            normable = str(answer[1]) == "1"
            if not normable:
                for h_spec in h_specs:
                    key = (int(d), vertex_shift, h_spec)
                    if key in completed:
                        continue
                    row = {
                        "schemaVersion": "q6-a4c2-multistratum-v1",
                        "d": int(d),
                        "vertexShift": vertex_shift,
                        "h": list(h_spec),
                        "quotientPolynomial": str(tower["q"]),
                        "relativeNormable": False,
                        "status": "edge_value_not_relative_norm",
                        "networkCalls": 0,
                        "submissionCalls": 0,
                    }
                    existing.append(row)
                    completed.add(key)
                atomic_text(
                    OUTPUT,
                    "".join(
                        json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n"
                        for item in existing
                    ),
                )
                print(
                    json.dumps(
                        {
                            "d": int(d),
                            "t": vertex_shift,
                            "status": "not_norm",
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                continue

            seed = relative(answer[0])
            if seed.relative_norm() != base_edge_value:
                raise ArithmeticError("norm seed reconstruction failed")
            for h_spec in h_specs:
                key = (int(d), vertex_shift, h_spec)
                if key in completed:
                    continue
                h0, h1 = h_spec
                h = base(h0) + h1 * z
                if h - generator == 0:
                    continue
                norm_one = (h + generator) / (h - generator)
                if norm_one.relative_norm() != 1:
                    raise ArithmeticError("base-valued Hilbert-90 twist lost norm one")
                value = tower["relativeToAbsolute"](seed * norm_one)
                scaled, square_scale, minimal = shared.integral_square_scale(value)
                candidate = candidate_ring(minimal(x**2))
                line = shared.canonical_line(candidate)
                digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                irreducible = bool(candidate.is_irreducible())
                real_roots = (
                    int(candidate.number_of_real_roots()) if irreducible else None
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
                    "schemaVersion": "q6-a4c2-multistratum-v1",
                    "d": int(d),
                    "vertexShift": vertex_shift,
                    "vertexProductSquare": int(common["quartic"](vertex_shift)),
                    "h": list(h_spec),
                    "quotientPolynomial": str(tower["q"]),
                    "quotientLabel": "12T6",
                    "relativeNormable": True,
                    "relativeNormVerified": True,
                    "squareScale": square_scale,
                    "candidateCoefficientLine": line,
                    "candidateSha256": digest,
                    "candidateBytes": len(line.encode("ascii")),
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
                        json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n"
                        for item in existing
                    ),
                )
                print(
                    json.dumps(
                        {
                            "d": int(d),
                            "t": vertex_shift,
                            "h": list(h_spec),
                            "hash": digest[:12],
                            "r": real_roots,
                            "survivors": [
                                item["label"] for item in survivors
                            ],
                            "tc0": [
                                item["label"]
                                for item in survivors
                                if item["gate"]["currentTc0"]
                            ],
                            "status": row["status"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

    unique_tc0 = [
        {
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
        "schemaVersion": "q6-a4c2-multistratum-search-v1",
        "rows": len(existing),
        "dValues": sorted({row["d"] for row in existing}),
        "normableRows": sum(row["relativeNormable"] for row in existing),
        "uniqueCurrentTc0": unique_tc0,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
