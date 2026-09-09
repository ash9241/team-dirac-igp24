#!/usr/bin/env sage -python
"""Exact full-wreath target certificates for the asymmetric pilot.

For h(x)=f(g(x)), the Galois group embeds in S_deg(g) wr S_deg(f).  For each
candidate this worker enumerates the proper transitive maximal subgroups of
that exact overgroup and uses squarefree Frobenius cycle types to exclude all
of them.  Completion therefore proves equality with the named 24T group.
No network call, submission, or manifest staging is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from sage.all import GF, PolynomialRing, ZZ, libgap, primes_first_n


ROOT = Path(__file__).resolve().parent


def coefficient_polynomial(line: str):
    ring = PolynomialRing(ZZ, "x")
    values = [ZZ(value) for value in line.split(",")]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate line is not monic degree 24")
    return ring(values)


def cycle_type(polynomial, prime: int):
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


def target_profiles(outer_degree: int, inner_degree: int):
    standard = libgap.WreathProduct(
        libgap.SymmetricGroup(inner_degree), libgap.SymmetricGroup(outer_degree)
    )
    target_t = int(libgap.TransitiveIdentification(standard))
    target = libgap.TransitiveGroup(24, target_t)
    if int(libgap.Size(target)) != int(libgap.Size(standard)):
        raise ValueError("standard wreath product and target library order disagree")
    points = libgap.eval("[1..24]")
    maximals = [
        subgroup
        for subgroup in libgap.MaximalSubgroupClassReps(target)
        if bool(libgap.IsTransitive(subgroup, points))
    ]
    profiles = []
    identities = []
    for subgroup in maximals:
        profile = set()
        for conjugacy_class in libgap.ConjugacyClasses(subgroup):
            representative = libgap.Representative(conjugacy_class)
            profile.add(
                tuple(
                    sorted(
                        int(value)
                        for value in libgap.CycleLengths(representative, points)
                    )
                )
            )
        profiles.append(profile)
        identities.append(
            {
                "label": f"24T{int(libgap.TransitiveIdentification(subgroup))}",
                "order": int(libgap.Size(subgroup)),
            }
        )
    return {
        "groupOrder": int(libgap.Size(target)),
        "identities": identities,
        "innerDegree": inner_degree,
        "outerDegree": outer_degree,
        "profiles": profiles,
        "properTransitiveMaximalCount": len(maximals),
        "targetLabel": f"24T{target_t}",
        "targetT": target_t,
    }


def certify(polynomial, target: dict, prime_count: int):
    profiles = target["profiles"]
    witnesses = [None] * len(profiles)
    checked = 0
    for prime in primes_first_n(prime_count):
        prime = int(prime)
        profile = cycle_type(polynomial, prime)
        if profile is None:
            continue
        checked += 1
        for index, maximal_profile in enumerate(profiles):
            if witnesses[index] is None and profile not in maximal_profile:
                witnesses[index] = {"cycleType": list(profile), "prime": prime}
        if all(witness is not None for witness in witnesses):
            break
    rows = [
        {**identity, "witness": witness}
        for identity, witness in zip(target["identities"], witnesses)
    ]
    return {
        "checkedSquarefreePrimes": checked,
        "complete": all(witness is not None for witness in witnesses),
        "properTransitiveMaximals": rows,
    }


def live_snapshot(connection, label: str, r: int):
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
               CASE WHEN b.label IS NULL THEN 0 ELSE 1 END,
               CASE WHEN owned.label IS NULL THEN 0 ELSE 1 END
        FROM targets AS t
        LEFT JOIN baseline_pairs AS b ON b.label=t.label AND b.r=t.r
        LEFT JOIN (
            SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
        ) AS owned ON owned.label=t.label AND owned.r=t.r
        WHERE t.label=? AND t.r=?
        """,
        (label, r),
    ).fetchone()
    if row is None:
        return {"pair": f"{label}/r{r}", "presentInCurrentRareTargetGrid": False}
    return {
        "baseline": bool(row[4]),
        "discovered": int(row[1]),
        "generatedAt": str(row[3]) if row[3] else None,
        "locallyOwned": bool(row[5]),
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "pair": f"{label}/r{r}",
        "presentInCurrentRareTargetGrid": True,
        "teamCount": int(row[0]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "data" / "agent_gold_b_asymmetric_composition_pilot.json",
    )
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--prime-count", type=int, default=2000)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "agent_gold_b_asymmetric_composition_certificates.json",
    )
    args = parser.parse_args()

    started = time.monotonic()
    pilot = json.loads(args.input.read_text(encoding="utf-8"))
    targets = {}
    for candidate in pilot["candidates"]:
        construction = candidate["exactConstruction"]
        key = (int(construction["outerDegree"]), int(construction["innerDegree"]))
        if key not in targets:
            targets[key] = target_profiles(*key)

    connection = sqlite3.connect(f"file:{args.db}?immutable=1", uri=True)
    rows = []
    for candidate in pilot["candidates"]:
        line = candidate["candidateCoefficientLine"]
        digest = hashlib.sha256(line.encode()).hexdigest()
        if digest != candidate["candidateSha256"]:
            raise ValueError("pilot candidate hash mismatch")
        polynomial = coefficient_polynomial(line)
        construction = candidate["exactConstruction"]
        key = (int(construction["outerDegree"]), int(construction["innerDegree"]))
        target = targets[key]
        certificate = certify(polynomial, target, args.prime_count)
        label = target["targetLabel"]
        real_roots = int(polynomial.number_of_real_roots())
        live = live_snapshot(connection, label, real_roots)
        rows.append(
            {
                "candidateIndex": int(candidate["index"]),
                "candidateSha256": digest,
                "containmentProof": {
                    "exactCompositionRelation": construction["relation"],
                    "overgroup": (
                        f"S_{construction['innerDegree']} wr "
                        f"S_{construction['outerDegree']} in its imprimitive "
                        "degree-24 action"
                    ),
                    "theorem": (
                        "the fibers above roots of the outer polynomial form "
                        "a Galois-invariant block system"
                    ),
                },
                "liveTarget": live,
                "maximalSubgroupCertificate": certificate,
                "realRoots": real_roots,
                "status": (
                    f"certified_{label}_r{real_roots}"
                    if certificate["complete"]
                    else f"contained_in_{label}_certificate_incomplete"
                ),
                "targetGroupOrder": target["groupOrder"],
                "targetLabel": label,
                "targetT": target["targetT"],
            }
        )
    connection.close()

    certified = [row for row in rows if row["maximalSubgroupCertificate"]["complete"]]
    live_gold = [
        row
        for row in certified
        if row["liveTarget"].get("presentInCurrentRareTargetGrid")
        and row["liveTarget"].get("teamCount") == 0
        and not row["liveTarget"].get("baseline")
        and not row["liveTarget"].get("locallyOwned")
    ]
    public_targets = {
        f"{outer}x{inner}": {
            key: value
            for key, value in target.items()
            if key not in ("profiles", "identities")
        }
        for (outer, inner), target in targets.items()
    }
    result = {
        "candidateCount": len(rows),
        "certifiedCount": len(certified),
        "certifiedLiveGoldCount": len(live_gold),
        "elapsedSeconds": time.monotonic() - started,
        "input": str(args.input.resolve()),
        "inputSha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "networkCalls": 0,
        "primeCountLimit": args.prime_count,
        "results": rows,
        "stagedPolynomials": 0,
        "submissionCalls": 0,
        "targetOvergroups": public_targets,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    args.output.resolve().write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "candidates": len(rows),
                "certified": len(certified),
                "elapsedSeconds": result["elapsedSeconds"],
                "liveGold": len(live_gold),
                "output": str(args.output.resolve()),
                "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0 if len(certified) == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
