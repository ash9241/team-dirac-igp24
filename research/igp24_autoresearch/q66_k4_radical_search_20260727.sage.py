#!/usr/bin/env sage
"""Resumable Q66/K4-cycle radical search.

The search constructs a rank-at-most-nine Kummer lift by:

1. recovering the degree-6 S4 edge subfield M of the pinned 12T66 field K;
2. recovering a quartic vertex field in the common S4 normal closure;
3. taking the six pair-products of its four conjugates;
4. solving N_{K/M}(a)=n for one such edge product n;
5. varying a through explicit relative norm-one Hilbert-90 multipliers.

Thus the four K4 vertex-star products are squares identically.  Every
candidate is checked offline and appended atomically to a resumable JSONL
ledger.  This script has no submission or network path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path

from sage.all import GF, NumberField, PolynomialRing, QQ, ZZ, libgap, pari, prime_range


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
RESULTS = ROOT / "data" / "q66_k4_radical_search_20260727.jsonl"
SUMMARY = ROOT / "data" / "q66_k4_radical_search_20260727_summary.json"
Q_LINE = (
    "100,2072,11684,12516,-27280,-63882,-39823,-772,"
    "8011,3086,491,36,1"
)
Q_SHA = "8fc86377ab3f561ba7bc05edcd4d0b35db94ddde4d7536f28c29b5679e17bd5f"
REQUESTED = (15051, 15082)
STRUCTURAL_CANDIDATES = (14969, 14971, 14973, 14974, 15051, 15052, 15070, 15082)


def atomic_text(path: Path, value: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def canonical_line(polynomial):
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def cycle_type(polynomial, prime):
    reduced = polynomial.change_ring(GF(prime))
    if not reduced.is_squarefree():
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in reduced.factor()
            for _ in range(int(exponent))
        )
    )


def group_profiles(target_t):
    group = libgap.TransitiveGroup(24, target_t)
    points = libgap.eval("[1..24]")
    return {
        tuple(
            sorted(
                int(value)
                for value in libgap.CycleLengths(
                    libgap.Representative(conjugacy_class), points
                )
            )
        )
        for conjugacy_class in libgap.ConjugacyClasses(group)
    }


def maximal_certificate(polynomial, target_t, observations):
    target = libgap.TransitiveGroup(24, target_t)
    points = libgap.eval("[1..24]")
    rows = []
    for subgroup in list(libgap.MaximalSubgroupClassReps(target)):
        if not bool(libgap.IsTransitive(subgroup, points)):
            continue
        profiles = {
            tuple(
                sorted(
                    int(value)
                    for value in libgap.CycleLengths(
                        libgap.Representative(conjugacy_class), points
                    )
                )
            )
            for conjugacy_class in list(libgap.ConjugacyClasses(subgroup))
        }
        witness = next(
            (
                {"prime": prime, "cycleType": list(profile)}
                for prime, profile in observations
                if profile not in profiles
            ),
            None,
        )
        rows.append(
            {
                "label": f"24T{int(libgap.TransitiveIdentification(subgroup))}",
                "order": int(libgap.Size(subgroup)),
                "witness": witness,
            }
        )
    return {
        "completeConditionalOnTargetContainment": all(
            row["witness"] is not None for row in rows
        ),
        "properTransitiveMaximals": rows,
    }


def integral_square_scale(value):
    minimal = value.minpoly()
    denominator = ZZ(1)
    for coefficient in minimal.list():
        denominator = denominator.lcm(QQ(coefficient).denominator())
    # Scaling an algebraic number by denominator^2 makes every transformed
    # minpoly coefficient integral; verify rather than assume.
    scale = denominator
    scaled = scale**2 * value
    polynomial = scaled.minpoly()
    if any(QQ(coefficient).denominator() != 1 for coefficient in polynomial):
        raise ArithmeticError("square scaling did not give an integral minpoly")
    return scaled, int(scale), polynomial.change_ring(ZZ)


def live_gate(label, real_roots, digest):
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    row = connection.execute(
        """
        SELECT team_count,discovered,generated_at
        FROM targets WHERE label=? AND r=?
        """,
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
        "teamCount": None if row is None else int(row[0]),
        "discovered": None if row is None else int(row[1]),
        "generatedAt": None if row is None else row[2],
        "baseline": baseline is not None,
        "owned": owned is not None,
        "knownHash": known is not None,
        "currentTc0": bool(
            row
            and int(row[0]) == 0
            and int(row[1]) == 0
            and baseline is None
            and owned is None
        ),
    }


def build_tower():
    ring = PolynomialRing(QQ, "y")
    q = ring([ZZ(value) for value in Q_LINE.split(",")])
    if hashlib.sha256(Q_LINE.encode("ascii")).hexdigest() != Q_SHA:
        raise ValueError("pinned q hash changed")
    field = NumberField(q, "alpha")
    edge_field, edge_embedding, _ = field.subfields(6)[0]
    relative = field.relativize(edge_embedding(edge_field.gen()), "beta")
    base = relative.base_field()
    norm_data = base.pari_rnfnorm_data(relative)

    normal_closure, _ = edge_field.galois_closure(names="omega", map=True)
    quartics = [entry[0].polynomial() for entry in normal_closure.subfields(4)]
    quartic = min(
        quartics,
        key=lambda polynomial: (
            max(abs(QQ(value)) for value in polynomial),
            str(polynomial),
        ),
    )
    edge_resolvent = quartic.symmetric_power(2, monic=True)
    linear_roots = [
        root
        for root, multiplicity in edge_resolvent.change_ring(base).roots()
        if multiplicity == 1
    ]
    norm_solutions = []
    for edge_value in linear_roots:
        answer = norm_data.rnfisnorm(pari(edge_value))
        if str(answer[1]) == "1":
            norm_solutions.append((edge_value, relative(answer[0])))
    if not norm_solutions:
        raise ArithmeticError("neither K4 edge value is a relative norm")
    edge_value, seed = min(norm_solutions, key=lambda item: len(str(item[1])))
    if seed.relative_norm() != edge_value:
        raise ArithmeticError("relative norm solution reconstruction failed")
    relative_to_absolute = relative.structure()[0]
    coefficient = relative.relative_polynomial()[1]
    conjugate_generator = -coefficient - relative.gen()
    return {
        "q": q,
        "field": field,
        "edgeField": edge_field,
        "relative": relative,
        "relativeToAbsolute": relative_to_absolute,
        "quartic": quartic,
        "edgeResolvent": edge_resolvent,
        "edgeValue": edge_value,
        "seed": seed,
        "conjugateGenerator": conjugate_generator,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--c-min", type=int, default=-20)
    parser.add_argument("--c-max", type=int, default=20)
    parser.add_argument("--prime-limit", type=int, default=1000)
    args = parser.parse_args()
    if args.c_min > args.c_max:
        raise ValueError("empty c range")

    completed = set()
    existing = []
    if RESULTS.exists():
        existing = [
            json.loads(line)
            for line in RESULTS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        completed = {int(row["hilbert90Parameter"]) for row in existing}

    tower = build_tower()
    profile_bank = {
        target_t: group_profiles(target_t) for target_t in STRUCTURAL_CANDIDATES
    }
    polynomial_ring = PolynomialRing(ZZ, "x")
    x = polynomial_ring.gen()
    generated = []
    for parameter in range(args.c_min, args.c_max + 1):
        if parameter in completed:
            continue
        relative = tower["relative"]
        element = relative(parameter) + relative.gen()
        conjugate = relative(parameter) + tower["conjugateGenerator"]
        if conjugate == 0:
            continue
        norm_one = element / conjugate
        if norm_one.relative_norm() != 1:
            raise ArithmeticError("Hilbert-90 multiplier norm is not one")
        relative_value = tower["seed"] * norm_one
        if relative_value.relative_norm() != tower["edgeValue"]:
            raise ArithmeticError("structured relative norm changed")
        absolute_value = tower["relativeToAbsolute"](relative_value)
        scaled, square_scale, minimal = integral_square_scale(absolute_value)
        candidate = polynomial_ring(minimal(x**2))
        line = canonical_line(candidate)
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        irreducible = bool(candidate.is_irreducible())
        real_roots = int(candidate.number_of_real_roots()) if irreducible else None
        observations = []
        possible = set(STRUCTURAL_CANDIDATES)
        if irreducible:
            for prime in prime_range(2, args.prime_limit + 1):
                profile = cycle_type(candidate, int(prime))
                if profile is None:
                    continue
                observations.append((int(prime), profile))
                possible = {
                    target_t
                    for target_t in possible
                    if profile in profile_bank[target_t]
                }
                if not possible:
                    break
        requested_possible = sorted(set(REQUESTED) & possible)
        certificates = {
            f"24T{target_t}": maximal_certificate(
                candidate, target_t, observations
            )
            for target_t in requested_possible
        }
        gates = {
            f"24T{target_t}": live_gate(
                f"24T{target_t}", int(real_roots), digest
            )
            for target_t in requested_possible
            if real_roots is not None
        }
        row = {
            "schemaVersion": "q66-k4-cycle-radical-candidate-v1",
            "hilbert90Parameter": parameter,
            "qCoefficientLine": Q_LINE,
            "qSha256": Q_SHA,
            "qLabel": "12T66",
            "quarticVertexPolynomial": str(tower["quartic"]),
            "edgePairResolventPolynomial": str(tower["edgeResolvent"]),
            "relativeEdgeNorm": str(tower["edgeValue"]),
            "relativeNormVerified": True,
            "k4Invariant": (
                "the six edge-pair norms are pair-products of four vertex "
                "conjugates; after the opposite-edge identification, all "
                "four vertex-star products are squares"
            ),
            "squareScale": square_scale,
            "minimalRadicandPolynomial": canonical_line(minimal),
            "candidateCoefficientLine": line,
            "candidateSha256": digest,
            "candidateBytes": len(line.encode("ascii")),
            "degree": int(candidate.degree()),
            "monic": bool(candidate.is_monic()),
            "primitive": math.gcd(*[abs(int(value)) for value in candidate]) == 1,
            "irreducible": irreducible,
            "realRoots": real_roots,
            "checkedGoodPrimes": len(observations),
            "structuralCandidateLabelsAfterFrobenius": [
                f"24T{target_t}" for target_t in sorted(possible)
            ],
            "requestedLabelsAfterFrobenius": [
                f"24T{target_t}" for target_t in requested_possible
            ],
            "conditionalMaximalCertificates": certificates,
            "liveGates": gates,
            "status": (
                "promising_requested_target_survivor"
                if requested_possible
                else "rejected_requested_targets"
            ),
            "networkCalls": 0,
            "submissionCalls": 0,
        }
        existing.append(row)
        generated.append(row)
        atomic_text(
            RESULTS,
            "".join(
                json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n"
                for item in existing
            ),
        )
        print(
            json.dumps(
                {
                    "c": parameter,
                    "hash": digest,
                    "r": real_roots,
                    "possible": row["structuralCandidateLabelsAfterFrobenius"],
                    "requested": row["requestedLabelsAfterFrobenius"],
                    "status": row["status"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    all_rows = existing
    summary = {
        "schemaVersion": "q66-k4-cycle-radical-search-v1",
        "qSha256": Q_SHA,
        "resultRows": len(all_rows),
        "newRows": len(generated),
        "requestedSurvivors": [
            {
                "c": row["hilbert90Parameter"],
                "hash": row["candidateSha256"],
                "r": row["realRoots"],
                "labels": row["requestedLabelsAfterFrobenius"],
            }
            for row in all_rows
            if row["requestedLabelsAfterFrobenius"]
        ],
        "results": str(RESULTS.relative_to(ROOT)),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
