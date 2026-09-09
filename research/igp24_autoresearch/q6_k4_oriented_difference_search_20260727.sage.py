#!/usr/bin/env sage
"""Exact 12T6 directed-edge K4 radical search for target 24T10381.

Let f(t)=t^4-4t^2+t+1, with S4 Galois group, and let y=alpha_i-alpha_j
run over ordered differences of distinct roots.  The polynomial q(y) has
action 12T6.  The element

    B(y)=7244*alpha_i

is recovered exactly in Q(y).  For an integer c set

    a_c(y)=B(y)*(c^2-y^2).

For a fixed K4 vertex i, the product of a_c over the six directed edges
incident with i is

    (7244^3 * alpha_i * product_{j != i}(c^2-(alpha_i-alpha_j)^2))^2,

because product_i(alpha_i)=1.  Hence the four vertex-star relations hold
identically, giving the exact rank-at-most-nine K4 cycle module.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sqlite3
from pathlib import Path

from sage.all import GF, Matrix, NumberField, PolynomialRing, QQ, ZZ, libgap, prime_range


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "q6_k4_oriented_difference_search_20260727.jsonl"
SUMMARY = ROOT / "data" / "q6_k4_oriented_difference_search_20260727_summary.json"
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


def orientation_proof():
    s4 = libgap.SymmetricGroup(4)
    ordered_edges = libgap.eval(
        "Filtered(Cartesian([1..4],[1..4]), x -> x[1] <> x[2])"
    )
    directed = libgap.Action(s4, ordered_edges, libgap.OnTuples)
    target = libgap.TransitiveGroup(24, TARGET)
    blocks = libgap.AsSet(
        [
            libgap.Set([2 * index - 1, 2 * index])
            for index in range(1, 13)
        ]
    )
    quotient_hom = libgap.ActionHomomorphism(target, blocks, libgap.OnSets)
    quotient = libgap.Image(quotient_hom)
    kernel = libgap.Kernel(quotient_hom)

    # Recover the kernel code and its four weight-six dual supports.
    rows = []
    block_list = [[2 * index - 1, 2 * index] for index in range(1, 13)]
    for generator in list(libgap.GeneratorsOfGroup(kernel)):
        rows.append(
            [
                int(libgap.OnPoints(block[0], generator)) == block[1]
                for block in block_list
            ]
        )
    matrix = Matrix(GF(2), [[int(value) for value in row] for row in rows])
    code = matrix.row_space()
    dual = Matrix(GF(2), list(code.basis())).right_kernel()
    weight_six = sorted(
        [
            [index + 1 for index, value in enumerate(vector) if value]
            for vector in dual
            if sum(vector) == 6
        ]
    )
    intersections = sorted(
        len(set(weight_six[i]) & set(weight_six[j]))
        for i in range(4)
        for j in range(i + 1, 4)
    )
    coordinate_multiplicities = sorted(
        sum(coordinate in support for support in weight_six)
        for coordinate in range(1, 13)
    )
    return {
        "quarticActionLabel": f"4T{int(libgap.TransitiveIdentification(s4))}",
        "directedEdgeActionLabel": (
            f"12T{int(libgap.TransitiveIdentification(directed))}"
        ),
        "targetQuotientLabel": (
            f"12T{int(libgap.TransitiveIdentification(quotient))}"
        ),
        "targetQuotientOrder": int(libgap.Size(quotient)),
        "kernelOrder": int(libgap.Size(kernel)),
        "kernelRank": int(code.dimension()),
        "dualRank": int(dual.dimension()),
        "weightSixDualSupports": weight_six,
        "pairwiseStarIntersectionSizes": intersections,
        "coordinateStarMultiplicities": coordinate_multiplicities,
        "isExactK4StarIncidence": (
            len(weight_six) == 4
            and intersections == [2] * 6
            and coordinate_multiplicities == [2] * 12
        ),
    }


def live_gate(real_roots, digest):
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    target = connection.execute(
        """
        SELECT team_count,discovered,generated_at
        FROM targets WHERE label=? AND r=?
        """,
        (f"24T{TARGET}", int(real_roots)),
    ).fetchone()
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
        (f"24T{TARGET}", int(real_roots)),
    ).fetchone()
    owned = connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1",
        (f"24T{TARGET}", int(real_roots)),
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

    orientation = orientation_proof()
    if (
        orientation["directedEdgeActionLabel"] != "12T6"
        or orientation["targetQuotientLabel"] != "12T6"
        or not orientation["isExactK4StarIncidence"]
    ):
        raise ArithmeticError("12T6/K4 action orientation proof failed")

    rational_ring = PolynomialRing(QQ, "y")
    y = rational_ring.gen()
    q = (
        y**12
        - 32 * y**10
        + 360 * y**8
        - 1830 * y**6
        + 4432 * y**4
        - 4872 * y**2
        + 1957
    )
    tail = (
        -321 * y**10
        + 9992 * y**8
        - 106596 * y**6
        + 487899 * y**4
        - 945096 * y**2
        + 3622 * y
        + 594920
    )
    quartic = PolynomialRing(QQ, "t")(
        [1, 1, -4, 0, 1]
    )
    if quartic.galois_group().transitive_number() != 5:
        raise ArithmeticError("quartic is no longer S4")
    field = NumberField(q, names="d")
    d = field.gen()
    recovered_tail = field(tail)
    if quartic(recovered_tail / 7244) != 0:
        raise ArithmeticError("tail root recovery identity failed")

    target_profiles = shared.group_profiles(TARGET)
    candidate_ring = PolynomialRing(ZZ, "x")
    x = candidate_ring.gen()
    resultant_ring = PolynomialRing(candidate_ring, "z")
    z = resultant_ring.gen()
    qz = resultant_ring(q(z))
    existing = []
    completed = set()
    if OUTPUT.exists():
        existing = [
            json.loads(line)
            for line in OUTPUT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        completed = {int(row["parameter"]) for row in existing}

    for parameter in range(args.c_min, args.c_max + 1):
        if parameter in completed:
            continue
        radicand_univariate = (tail * (parameter**2 - y**2)) % q
        radicand = resultant_ring(radicand_univariate(z))
        candidate = candidate_ring(qz.resultant(x**2 - radicand))
        line = shared.canonical_line(candidate)
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        observations = []
        possible = True
        for prime in prime_range(2, args.prime_limit + 1):
            profile = shared.cycle_type(candidate, int(prime))
            if profile is None:
                continue
            observations.append((int(prime), profile))
            if profile not in target_profiles:
                possible = False
                break
        irreducible = bool(candidate.is_irreducible()) if possible else None
        real_roots = (
            int(candidate.number_of_real_roots())
            if possible and irreducible
            else None
        )
        maximal = (
            shared.maximal_certificate(candidate, TARGET, observations)
            if possible and irreducible
            else None
        )
        gate = (
            live_gate(real_roots, digest)
            if possible and irreducible
            else None
        )
        row = {
            "schemaVersion": "q6-k4-oriented-difference-v1",
            "parameter": parameter,
            "quarticPolynomial": str(quartic),
            "quarticDiscriminant": int(quartic.discriminant()),
            "quotientPolynomial": str(q),
            "quotientLabel": "12T6",
            "orientationProof": orientation,
            "tailElementPolynomial": str(tail),
            "tailIdentity": "tail(y)=7244*alpha_i",
            "radicandPolynomial": str(radicand),
            "starSquareIdentityVerifiedSymbolically": True,
            "candidateCoefficientLine": line,
            "candidateSha256": digest,
            "candidateBytes": len(line.encode("ascii")),
            "degree": int(candidate.degree()),
            "monic": bool(candidate.is_monic()),
            "primitive": math.gcd(*[abs(int(value)) for value in candidate]) == 1,
            "checkedGoodPrimes": len(observations),
            "targetSurvivesFrobenius": possible,
            "decisiveExclusion": (
                None
                if possible
                else {
                    "prime": observations[-1][0],
                    "cycleType": list(observations[-1][1]),
                }
            ),
            "irreducible": irreducible,
            "realRoots": real_roots,
            "conditionalMaximalCertificate": maximal,
            "liveGate": gate,
            "status": (
                "submit_candidate"
                if possible
                and irreducible
                and maximal["completeConditionalOnTargetContainment"]
                and gate["currentTc0"]
                else (
                    "target_survivor_incomplete_certificate"
                    if possible and irreducible
                    else "excluded"
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
                    "survives": possible,
                    "status": row["status"],
                    "decisive": row["decisiveExclusion"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    summary = {
        "schemaVersion": "q6-k4-oriented-difference-search-v1",
        "orientationProof": orientation,
        "rows": len(existing),
        "survivors": [
            {
                "parameter": row["parameter"],
                "candidateSha256": row["candidateSha256"],
                "realRoots": row["realRoots"],
                "status": row["status"],
            }
            for row in existing
            if row["targetSurvivesFrobenius"]
        ],
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
