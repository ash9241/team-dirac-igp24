#!/usr/bin/env sage
"""Exact 12T6 = C2 x A4 K4-star radical search for 24T10381.

This is deliberately separate from the invalid ordinary directed-edge
construction (which has quotient 12T8).  Here an A4 quartic splitting field L
is composed with Q(sqrt(2)).  If v is a double transposition and v(w)=-w,
then beta=sqrt(2)*w is fixed by the diagonal involution (v,tau).  The resulting
degree-12 coset action is exactly 12T6.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sqlite3
from pathlib import Path

from sage.all import (
    GF,
    Matrix,
    NumberField,
    PolynomialRing,
    QQ,
    ZZ,
    libgap,
    pari,
    prime_range,
)


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
STRUCTURES = ROOT / "data" / "agent_non12_tower_structures.jsonl"
OUTPUT = ROOT / "data" / "q6_a4c2_k4_search_20260727.jsonl"
SUMMARY = ROOT / "data" / "q6_a4c2_k4_search_20260727_summary.json"
TARGET = 10381

shared_spec = importlib.util.spec_from_file_location(
    "q66_shared", ROOT / "q66_k4_radical_search_20260727.sage.py"
)
shared = importlib.util.module_from_spec(shared_spec)
shared_spec.loader.exec_module(shared)


def atomic_text(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def gap_cycle_type(permutation):
    return tuple(
        sorted(
            int(value)
            for value in libgap.CycleLengths(
                permutation, libgap.eval("[1..24]")
            )
        )
    )


def compatible_catalog():
    compatible = {}
    for line in STRUCTURES.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        for system in row.get("blockSystems", []):
            if (
                system.get("shape") == "12x2"
                and system.get("quotientActionLabel") == "12T6"
                and int(system.get("blockKernelOrder", 0)) <= 2**9
            ):
                compatible[int(row["t"])] = {
                    "label": str(row["label"]),
                    "kernelOrder": int(system["blockKernelOrder"]),
                    "order": int(row["groupOrder"]),
                }
                break
    profiles = {}
    for target_t in compatible:
        group = libgap.TransitiveGroup(24, target_t)
        profiles[target_t] = {
            gap_cycle_type(libgap.Representative(conjugacy_class))
            for conjugacy_class in libgap.ConjugacyClasses(group)
        }
    return compatible, profiles


def target_star_proof():
    target = libgap.TransitiveGroup(24, TARGET)
    blocks_list = [[2 * index - 1, 2 * index] for index in range(1, 13)]
    blocks = libgap.AsSet([libgap.Set(block) for block in blocks_list])
    homomorphism = libgap.ActionHomomorphism(target, blocks, libgap.OnSets)
    quotient = libgap.Image(homomorphism)
    kernel = libgap.Kernel(homomorphism)
    rows = []
    for generator in list(libgap.GeneratorsOfGroup(kernel)):
        rows.append(
            [
                int(libgap.OnPoints(block[0], generator)) == block[1]
                for block in blocks_list
            ]
        )
    code = Matrix(GF(2), [[int(value) for value in row] for row in rows]).row_space()
    dual = Matrix(GF(2), list(code.basis())).right_kernel()
    stars = sorted(
        [
            [index + 1 for index, value in enumerate(vector) if value]
            for vector in dual
            if sum(int(value) for value in vector) == 6
        ]
    )
    intersections = sorted(
        len(set(stars[i]) & set(stars[j]))
        for i in range(len(stars))
        for j in range(i + 1, len(stars))
    )
    multiplicities = sorted(
        sum(coordinate in support for support in stars)
        for coordinate in range(1, 13)
    )

    a4 = libgap.AlternatingGroup(4)
    c2 = libgap.CyclicGroup(2)
    direct = libgap.DirectProduct(a4, c2)
    first = libgap.Embedding(direct, 1)
    second = libgap.Embedding(direct, 2)
    v = libgap.eval("(1,2)(3,4)")
    diagonal_generator = (
        libgap.Image(first, v)
        * libgap.Image(second, libgap.GeneratorsOfGroup(c2)[0])
    )
    diagonal = libgap.Group([diagonal_generator])
    action = libgap.Action(
        direct, libgap.RightCosets(direct, diagonal), libgap.OnRight
    )
    return {
        "targetQuotientLabel": (
            f"12T{int(libgap.TransitiveIdentification(quotient))}"
        ),
        "targetQuotientStructure": str(libgap.StructureDescription(quotient)),
        "diagonalCosetActionLabel": (
            f"12T{int(libgap.TransitiveIdentification(action))}"
        ),
        "diagonalCosetActionOrder": int(libgap.Size(action)),
        "kernelOrder": int(libgap.Size(kernel)),
        "kernelRank": int(code.dimension()),
        "dualRank": int(dual.dimension()),
        "weightSixStars": stars,
        "pairwiseStarIntersections": intersections,
        "coordinateStarMultiplicities": multiplicities,
        "exactK4StarIncidence": (
            len(stars) == 4
            and intersections == [2] * 6
            and multiplicities == [2] * 12
        ),
    }


def build_tower():
    ring = PolynomialRing(QQ, "t")
    t = ring.gen()
    quartic = t**4 - 3 * t**3 + 3 * t**2 + 2 * t + 1
    if quartic.galois_group().transitive_number() != 4:
        raise ArithmeticError("quartic is no longer 4T4 = A4")
    quartic_field = NumberField(quartic, "a")
    closure, embedding = quartic_field.galois_closure(names="omega", map=True)
    roots = [root for root, multiplicity in quartic.change_ring(closure).roots()]
    if len(roots) != 4:
        raise ArithmeticError("quartic did not split into four roots")

    w = (roots[0] - roots[1]) + 2 * (roots[2] - roots[3])
    w_minimal = w.minpoly()
    if w_minimal.degree() != 12 or any(
        w_minimal[index] != 0 for index in range(1, 12, 2)
    ):
        raise ArithmeticError("anti-invariant does not have the expected even degree 12")
    d = ZZ(2)
    x = ring.gen()
    q = ring(
        sum(
            w_minimal[2 * index] * d ** (6 - index) * x ** (2 * index)
            for index in range(7)
        )
    )
    field = NumberField(q, "beta")
    beta = field.gen()
    relative = field.relativize(beta**2, "gamma")
    base = relative.base_field()

    edge_field, edge_embedding = closure.subfield(w**2)
    edge_norm = edge_embedding.preimage(roots[0] * roots[1])
    z = base.gen()
    base_edge_norm = base(
        sum(
            edge_norm[index] * (z / d) ** index
            for index in range(len(edge_norm.list()))
        )
    )
    norm_data = base.pari_rnfnorm_data(relative)
    answer = norm_data.rnfisnorm(pari(base_edge_norm))
    if str(answer[1]) != "1":
        raise ArithmeticError("K4 edge product is not a relative norm")
    seed = relative(answer[0])
    if seed.relative_norm() != base_edge_norm:
        raise ArithmeticError("relative norm seed reconstruction failed")
    return {
        "quartic": quartic,
        "quarticDiscriminant": int(quartic.discriminant()),
        "closureDegree": int(closure.degree()),
        "wMinimal": w_minimal,
        "d": int(d),
        "q": q,
        "field": field,
        "relative": relative,
        "baseEdgeNorm": base_edge_norm,
        "seed": seed,
        "relativeToAbsolute": relative.structure()[0],
    }


def live_gate(label, real_roots, digest):
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    target = connection.execute(
        "SELECT team_count,discovered,generated_at FROM targets WHERE label=? AND r=?",
        (label, real_roots),
    ).fetchone()
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
        (label, real_roots),
    ).fetchone()
    owned = connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1",
        (label, real_roots),
    ).fetchone()
    known = connection.execute(
        "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
    ).fetchone()
    connection.close()
    return {
        "teamCount": None if target is None else int(target[0]),
        "discovered": None if target is None else int(target[1]),
        "generatedAt": None if target is None else target[2],
        "baseline": baseline is not None,
        "owned": owned is not None,
        "knownHash": known is not None,
        "currentTc0": bool(
            target
            and int(target[0]) == 0
            and int(target[1]) == 0
            and baseline is None
            and owned is None
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--c-min", type=int, default=1)
    parser.add_argument("--c-max", type=int, default=80)
    parser.add_argument("--prime-limit", type=int, default=5000)
    args = parser.parse_args()

    star_proof = target_star_proof()
    if (
        star_proof["targetQuotientLabel"] != "12T6"
        or star_proof["targetQuotientStructure"] != "C2 x A4"
        or star_proof["diagonalCosetActionLabel"] != "12T6"
        or not star_proof["exactK4StarIncidence"]
    ):
        raise ArithmeticError("exact 12T6/K4 orientation gate failed")
    tower = build_tower()
    compatible, profiles = compatible_catalog()
    if TARGET not in compatible:
        raise ArithmeticError("target absent from exhaustive compatible catalog")

    existing = []
    completed = set()
    if OUTPUT.exists():
        existing = [
            json.loads(line)
            for line in OUTPUT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        completed = {int(row["parameter"]) for row in existing}

    candidate_ring = PolynomialRing(ZZ, "x")
    x = candidate_ring.gen()
    relative = tower["relative"]
    generator = relative.gen()
    conjugate_generator = -generator
    for parameter in range(args.c_min, args.c_max + 1):
        if parameter in completed:
            continue
        element = relative(parameter) + generator
        conjugate = relative(parameter) + conjugate_generator
        norm_one = element / conjugate
        if norm_one.relative_norm() != 1:
            raise ArithmeticError("Hilbert-90 multiplier lost norm one")
        relative_value = tower["seed"] * norm_one
        if relative_value.relative_norm() != tower["baseEdgeNorm"]:
            raise ArithmeticError("edge norm changed during Hilbert-90 scan")
        absolute_value = tower["relativeToAbsolute"](relative_value)
        scaled, square_scale, minimal = shared.integral_square_scale(absolute_value)
        candidate = candidate_ring(minimal(x**2))
        line = shared.canonical_line(candidate)
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        irreducible = bool(candidate.is_irreducible())
        real_roots = int(candidate.number_of_real_roots()) if irreducible else None

        possible = set(compatible) if irreducible else set()
        if real_roots is not None:
            archimedean = tuple(
                sorted([1] * real_roots + [2] * ((24 - real_roots) // 2))
            )
            possible = {
                target_t
                for target_t in possible
                if archimedean in profiles[target_t]
            }
        observations = []
        eliminations = []
        for prime in prime_range(2, args.prime_limit + 1):
            if not possible:
                break
            profile = shared.cycle_type(candidate, int(prime))
            if profile is None:
                continue
            before = set(possible)
            possible = {
                target_t
                for target_t in possible
                if profile in profiles[target_t]
            }
            observations.append((int(prime), profile))
            removed = sorted(before - possible)
            if removed:
                eliminations.append(
                    {
                        "prime": int(prime),
                        "cycleType": list(profile),
                        "removed": [f"24T{target_t}" for target_t in removed],
                    }
                )
            if len(possible) <= 1:
                break

        survivors = [
            {
                **compatible[target_t],
                "gate": live_gate(
                    compatible[target_t]["label"], real_roots, digest
                ),
            }
            for target_t in sorted(possible)
        ]
        unique_target = len(possible) == 1 and TARGET in possible
        row = {
            "schemaVersion": "q6-a4c2-k4-radical-v1",
            "parameter": parameter,
            "quarticPolynomial": str(tower["quartic"]),
            "quarticLabel": "4T4",
            "quarticDiscriminant": tower["quarticDiscriminant"],
            "normalClosureDegree": tower["closureDegree"],
            "antiInvariantMinimalPolynomial": str(tower["wMinimal"]),
            "quadraticCompositeD": tower["d"],
            "quotientPolynomial": str(tower["q"]),
            "quotientLabel": "12T6",
            "starProof": star_proof,
            "edgeNorm": str(tower["baseEdgeNorm"]),
            "relativeNormVerified": True,
            "squareScale": square_scale,
            "minimalRadicandPolynomial": shared.canonical_line(minimal),
            "candidateCoefficientLine": line,
            "candidateSha256": digest,
            "candidateBytes": len(line.encode("ascii")),
            "degree": int(candidate.degree()),
            "monic": bool(candidate.is_monic()),
            "primitive": math.gcd(*[abs(int(value)) for value in candidate]) == 1,
            "irreducible": irreducible,
            "realRoots": real_roots,
            "catalogSize": len(compatible),
            "checkedGoodPrimes": len(observations),
            "observations": [
                {"prime": prime, "cycleType": list(profile)}
                for prime, profile in observations
            ],
            "eliminations": eliminations,
            "catalogSurvivors": survivors,
            "status": (
                "unique_exact_catalog_target_tc0"
                if unique_target and survivors[0]["gate"]["currentTc0"]
                else (
                    "unique_exact_catalog_target_not_tc0"
                    if unique_target
                    else "excluded_or_ambiguous"
                )
            ),
            "networkCalls": 0,
            "submissionCalls": 0,
        }
        existing.append(row)
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
                    "c": parameter,
                    "hash": digest[:16],
                    "r": real_roots,
                    "survivors": [
                        item["label"] for item in row["catalogSurvivors"]
                    ],
                    "status": row["status"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    summary = {
        "schemaVersion": "q6-a4c2-k4-search-v1",
        "catalogSize": len(compatible),
        "catalog": [compatible[target_t] for target_t in sorted(compatible)],
        "rows": len(existing),
        "uniqueTargetTc0": [
            {
                "parameter": row["parameter"],
                "candidateSha256": row["candidateSha256"],
                "realRoots": row["realRoots"],
                "candidateBytes": row["candidateBytes"],
            }
            for row in existing
            if row["status"] == "unique_exact_catalog_target_tc0"
        ],
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
