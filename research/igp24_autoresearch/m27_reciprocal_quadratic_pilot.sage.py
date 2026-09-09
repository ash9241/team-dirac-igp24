#!/usr/bin/env sage -python
"""Bounded offline reciprocal-quadratic pilot for the low order-384 targets.

For a certified degree-12 quotient q, construct

    f_{q,a}(x) = x^12 q(x + a/x).

The accepted even degree-24 parent and the uniqueness of its degree-12 block
action label certify the polynomial-specific Galois label of q.  This pilot
only applies exact irreducibility/signature checks and necessary Frobenius
cycle-profile exclusions.  It performs no network, submission, or general
Galois-group computation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from sage.all import GF, PolynomialRing, ZZ, libgap, prime_range


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
RING = PolynomialRing(ZZ, "x")
X = RING.gen()
PRIME_BOUND = 2000
MAX_UNRAMIFIED_PROBES = 200

SOURCES = (
    {
        "sourceKind": "official_galoisdb_group_page",
        "sourceOrdinal": 1,
        "quotientLabel": "12T26",
        "quotientCoefficientLine": (
            "1,-8,29,-49,40,15,2,-18,16,-3,1,-2,1"
        ),
        "quotientCoefficientSha256": (
            "c403ca489cd53475c516d9b074f824b02ba349f9e6cee241f3a3249c30e7ec28"
        ),
        "targetSignatures": {
            "24T907": (8, 16, 24),
            "24T917": (16, 24),
        },
    },
    {
        "sourceKind": "official_galoisdb_group_page",
        "sourceOrdinal": 2,
        "quotientLabel": "12T26",
        "quotientCoefficientLine": (
            "1,-4,4,4,-1,-9,-15,25,10,-11,-6,2,1"
        ),
        "quotientCoefficientSha256": (
            "02c13d6b41e063f09d8c878ebbefb0007f969d539dd798b47ef8e42cfc09ea32"
        ),
        "targetSignatures": {
            "24T907": (8, 16, 24),
            "24T917": (16, 24),
        },
    },
    {
        "sourceKind": "official_galoisdb_group_page",
        "sourceOrdinal": 3,
        "quotientLabel": "12T26",
        "quotientCoefficientLine": (
            "1,-5,-49,128,190,-474,-84,403,-24,-96,0,8,1"
        ),
        "quotientCoefficientSha256": (
            "c277cc16f15228f80720c296268dc5f7b639da69b6b9bc799991fd507f4fadbe"
        ),
        "targetSignatures": {
            "24T907": (8, 16, 24),
            "24T917": (16, 24),
        },
    },
    {
        "sourceKind": "official_galoisdb_group_page",
        "sourceOrdinal": 4,
        "quotientLabel": "12T26",
        "quotientCoefficientLine": (
            "9,0,63,123,324,-150,328,-189,150,-67,27,-6,1"
        ),
        "quotientCoefficientSha256": (
            "139c0f3154dd332b0a1b80a9d9bb6726b832ae58aa324151c442f10a08e5e886"
        ),
        "targetSignatures": {
            "24T907": (8, 16, 24),
            "24T917": (16, 24),
        },
    },
    {
        "sourceKind": "official_galoisdb_group_page",
        "sourceOrdinal": 5,
        "quotientLabel": "12T26",
        "quotientCoefficientLine": (
            "1,1,13,-28,68,-86,97,-76,53,-28,13,-4,1"
        ),
        "quotientCoefficientSha256": (
            "ed069df48a4554d2c29388e3f81364d262db884d72ba7aa61bb4b5c4c423348d"
        ),
        "targetSignatures": {
            "24T907": (8, 16, 24),
            "24T917": (16, 24),
        },
    },
    {
        "sourceKind": "official_galoisdb_group_page",
        "sourceOrdinal": 6,
        "quotientLabel": "12T26",
        "quotientCoefficientLine": (
            "1,-6,4,7,-2,5,-1,-1,-11,0,2,2,1"
        ),
        "quotientCoefficientSha256": (
            "71faa9675e8b34d4455c0bd5536b988ba8c67430ea7fc23010c241ecf2ff0e31"
        ),
        "targetSignatures": {
            "24T907": (8, 16, 24),
            "24T917": (16, 24),
        },
    },
    {
        "sourceKind": "official_galoisdb_group_page",
        "sourceOrdinal": 7,
        "quotientLabel": "12T26",
        "quotientCoefficientLine": (
            "1,-11,4,103,-19,-290,-63,278,151,-33,-26,1,1"
        ),
        "quotientCoefficientSha256": (
            "06ae0f954c7f3628a944eef68236a3603d7903ab0fc01e7eae8b2efdfd7625f0"
        ),
        "targetSignatures": {
            "24T907": (8, 16, 24),
            "24T917": (16, 24),
        },
    },
    {
        "sourceKind": "accepted_parent_unique_block_label",
        "quotientLabel": "12T30",
        "submissionId": "sub_a430d23df6194ff48047555f7ea8a879",
        "polynomialIndex": 210,
        "parentLabel": "24T18451",
        "parentR": 10,
        "parentCoefficientSha256": (
            "377ad9343511c9493254aae0078a900e27416e11452dd36ab06a1b22428e4d0d"
        ),
        "quotientCoefficientLine": (
            "-1,-13,-33,83,195,-182,-327,168,179,-47,-28,4,1"
        ),
        "quotientCoefficientSha256": (
            "a27618405696106c35fad623ebae530054bf10a981f153cbf2a173c9a028b1ba"
        ),
        "targetSignatures": {
            "24T943": (0, 8, 16),
        },
    },
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def coefficient_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def group_cycle_profiles(t: int) -> set[tuple[int, ...]]:
    group = libgap.TransitiveGroup(24, t)
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


def degree_twelve_quotient_labels(parent_t: int) -> list[str]:
    group = libgap.TransitiveGroup(24, parent_t)
    labels = set()
    for block in libgap.AllBlocks(group):
        if int(libgap.Length(block)) != 2:
            continue
        blocks = libgap.Orbit(group, block, libgap.OnSets)
        if int(libgap.Length(blocks)) != 12:
            continue
        action = libgap.ActionHomomorphism(group, libgap.AsSet(blocks), libgap.OnSets)
        quotient = libgap.Image(action)
        labels.add(f"12T{int(libgap.TransitiveIdentification(quotient))}")
    return sorted(labels)


def cycle_type(polynomial, prime: int) -> tuple[int, ...] | None:
    reduction = polynomial.change_ring(GF(prime))
    if not reduction.is_squarefree():
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in reduction.factor()
            for _ in range(int(exponent))
        )
    )


def main() -> int:
    profiles = {
        label: group_cycle_profiles(int(label[3:]))
        for source in SOURCES
        for label in source["targetSignatures"]
    }
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    source_certificates = []
    source_polynomials = []
    for expected in SOURCES:
        quotient_line = expected["quotientCoefficientLine"]
        if sha256_text(quotient_line) != expected["quotientCoefficientSha256"]:
            raise ValueError("pinned quotient coefficient hash changed")
        quotient = RING([ZZ(value) for value in quotient_line.split(",")])
        if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
            raise ArithmeticError("pinned quotient is not irreducible monic degree 12")
        if expected["sourceKind"] == "official_galoisdb_group_page":
            source_certificate = {
                "sourceKind": expected["sourceKind"],
                "sourceOrdinal": expected["sourceOrdinal"],
                "polynomialSpecificGroupProof": (
                    "Pinned directly from the official GaloisDB 12T26 group "
                    "page; no degree-24 parent block action is inferred."
                ),
                "quotientLabel": expected["quotientLabel"],
                "quotientCoefficientLine": quotient_line,
                "quotientCoefficientSha256": expected[
                    "quotientCoefficientSha256"
                ],
                "quotientIrreducible": True,
                "quotientRealRoots": int(quotient.number_of_real_roots()),
            }
        else:
            row = connection.execute(
                """
                SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable
                FROM polynomials AS p
                JOIN verifications AS v USING(submission_id,polynomial_index)
                WHERE p.submission_id=? AND p.polynomial_index=?
                """,
                (expected["submissionId"], expected["polynomialIndex"]),
            ).fetchone()
            if row is None:
                raise ValueError("pinned accepted parent is absent")
            if (
                str(row[1]) != expected["parentCoefficientSha256"]
                or str(row[2]) != expected["parentLabel"]
                or int(row[3]) != expected["parentR"]
                or str(row[4]) != "accepted"
                or int(row[5]) != 1
            ):
                raise ValueError("pinned accepted parent provenance changed")
            parent_coefficients = [ZZ(value) for value in str(row[0]).split(",")]
            if (
                len(parent_coefficients) != 25
                or parent_coefficients[-1] != 1
                or any(parent_coefficients[index] for index in range(1, 25, 2))
                or ",".join(str(value) for value in parent_coefficients[::2])
                != quotient_line
            ):
                raise ValueError("pinned parent/quotient relation changed")
            parent_t = int(expected["parentLabel"][3:])
            quotient_labels = degree_twelve_quotient_labels(parent_t)
            if quotient_labels != [expected["quotientLabel"]]:
                raise ArithmeticError(
                    f"parent has competing degree-12 block labels: {quotient_labels}"
                )
            source_certificate = {
                "sourceKind": expected["sourceKind"],
                "parentLabel": expected["parentLabel"],
                "parentR": expected["parentR"],
                "parentSubmissionId": expected["submissionId"],
                "parentPolynomialIndex": expected["polynomialIndex"],
                "parentCoefficientSha256": expected["parentCoefficientSha256"],
                "naturalPairingProof": (
                    "The accepted parent is exactly q(x^2), so its root pairs "
                    "{+sqrt(beta),-sqrt(beta)} give a Galois-invariant 12-block "
                    "system. Every 12-by-2 block action of the exact accepted "
                    "parent group has the displayed quotient label."
                ),
                "parentDegreeTwelveBlockLabels": quotient_labels,
                "quotientLabel": expected["quotientLabel"],
                "quotientCoefficientLine": quotient_line,
                "quotientCoefficientSha256": expected[
                    "quotientCoefficientSha256"
                ],
                "quotientIrreducible": True,
                "quotientRealRoots": int(quotient.number_of_real_roots()),
            }
        source_polynomials.append((expected, quotient))
        source_certificates.append(source_certificate)

    a_values = [
        sign * value
        for value in range(1, 31)
        if ZZ(value).is_squarefree()
        for sign in (1, -1)
    ]
    rows = []
    survivors = []
    for expected, quotient in source_polynomials:
        for a in a_values:
            candidate = RING(X**12 * quotient(X + ZZ(a) / X))
            if candidate.degree() != 24 or not candidate.is_monic():
                raise ArithmeticError("reciprocal construction lost monic degree 24")
            content = ZZ(candidate.content())
            if content != 1:
                candidate //= content
            signature = int(candidate.number_of_real_roots())
            applicable = sorted(
                label
                for label, signatures in expected["targetSignatures"].items()
                if signature in signatures
            )
            row = {
                "a": int(a),
                "coefficientSha256": sha256_text(coefficient_line(candidate)),
                "content": int(content),
                "irreducible": None,
                "quotientLabel": expected["quotientLabel"],
                "quotientCoefficientSha256": expected[
                    "quotientCoefficientSha256"
                ],
                "r": signature,
                "signatureApplicableTargets": applicable,
                "targetTests": {},
            }
            if not applicable:
                row["status"] = "signature_miss"
                rows.append(row)
                continue

            active = set(applicable)
            checked = {label: 0 for label in applicable}
            exclusions = {}
            observations = []
            for prime in prime_range(3, PRIME_BOUND + 1):
                prime = int(prime)
                profile = cycle_type(candidate, prime)
                if profile is None:
                    continue
                observations.append({"prime": prime, "cycleType": list(profile)})
                for label in list(active):
                    checked[label] += 1
                    if profile not in profiles[label]:
                        exclusions[label] = {
                            "prime": prime,
                            "cycleType": list(profile),
                        }
                        active.remove(label)
                if not active or all(
                    checked[label] >= MAX_UNRAMIFIED_PROBES for label in active
                ):
                    break
            irreducible = None
            if active:
                irreducible = bool(candidate.is_irreducible())
                row["irreducible"] = irreducible
                if not irreducible:
                    row["factorDegrees"] = sorted(
                        int(factor.degree())
                        for factor, exponent in candidate.factor()
                        for _ in range(int(exponent))
                    )
            for label in applicable:
                row["targetTests"][label] = {
                    "checkedUnramifiedPrimes": checked[label],
                    "decisiveExclusion": exclusions.get(label),
                    "survivesNecessaryCycleProfiles": label in active,
                    "survivesIrreducibilityAndCycleProfiles": (
                        label in active and irreducible is True
                    ),
                }
                if label in active and irreducible is True:
                    survivors.append(
                        {
                            "a": int(a),
                            "candidateCoefficientLine": coefficient_line(candidate),
                            "candidateCoefficientSha256": row[
                                "coefficientSha256"
                            ],
                            "candidateR": signature,
                            "checkedUnramifiedPrimes": checked[label],
                            "quotientCoefficientSha256": expected[
                                "quotientCoefficientSha256"
                            ],
                            "quotientLabel": expected["quotientLabel"],
                            "targetLabel": label,
                            "status": "cycle_profile_survivor_not_exact_label",
                        }
                    )
            row["status"] = (
                "exact_target_cycle_profile_exclusion"
                if not active
                else (
                    "cycle_profile_survivor"
                    if irreducible
                    else "reducible_after_cycle_profile_survival"
                )
            )
            rows.append(row)

    connection.close()
    summary = {
        "aValues": a_values,
        "candidateCount": len(rows),
        "cycleProfileSurvivorCount": len(survivors),
        "cycleProfileSurvivors": survivors,
        "exactLabelCount": 0,
        "method": (
            "exact accepted-parent quotient certification; exact integral "
            "reciprocal construction; exact real-root count; necessary unramified "
            "Frobenius cycle-profile exclusion; exact irreducibility for every "
            "cycle-profile survivor"
        ),
        "networkCalls": 0,
        "primeBound": PRIME_BOUND,
        "rows": rows,
        "sourceCertificates": source_certificates,
        "statusCounts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "submissionCalls": 0,
        "status": (
            "blocked_no_cycle_profile_survivor"
            if not survivors
            else "survivors_require_exact_group_certification"
        ),
    }
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
