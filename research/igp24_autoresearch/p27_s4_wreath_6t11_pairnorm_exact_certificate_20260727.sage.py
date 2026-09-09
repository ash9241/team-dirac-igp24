#!/usr/bin/env sage
"""Exact containment and maximal-subgroup certificate for the four v2 rows.

This is a fail-closed audit.  It does not search for new parameters, access the
network, stage a manifest, or submit anything.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import GF, Matrix, PolynomialRing, QQ, ZZ, libgap


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "data" / "p27_s4_wreath_6t11_pairnorm_pilot_20260727.jsonl"
OUTPUT = (
    ROOT
    / "data"
    / "p27_s4_wreath_6t11_pairnorm_exact_certificates_20260727.json"
)
DB = ROOT / "data" / "ledger.sqlite3"
TARGET_T = 24877
TARGET_LABEL = "24T24877"
TARGET_R = 14
ALTERNATIVE_COMPLEMENT_T = 24876


def atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def coefficient_line(polynomial):
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def polynomial_from_line(ring, line):
    return ring([ZZ(value) for value in line.split(",")])


def cycle_type(polynomial, prime):
    reduced = polynomial.change_ring(GF(prime))
    if not reduced.is_squarefree():
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, multiplicity in reduced.factor()
            for _ in range(int(multiplicity))
        )
    )


def image(point, permutation):
    return int(libgap.OnPoints(point, permutation))


def perm_from_images(images):
    return libgap.PermList(images)


def sign_code_group():
    """Build the entire parity-code preimage in S4 wr 6T11.

    It contains A4^6, every base sign vector whose sums on the three opposite
    pairs agree, and the standard lifts of all quotient generators.  Hence it
    is the whole semidirect preimage C <= F2^6, not a selected complement.
    """

    quotient = libgap.TransitiveGroup(6, 11)
    first_pair = tuple(
        sorted(int(value) for value in list(libgap.AllBlocks(quotient)[0]))
    )
    pairs = {
        tuple(sorted(image(point, element) for point in first_pair))
        for element in list(libgap.Elements(quotient))
    }
    pairs = sorted(pairs)
    if pairs != [(1, 4), (2, 5), (3, 6)]:
        raise ArithmeticError(f"unexpected 6T11 opposite-pair system: {pairs}")

    # C = {v in F2^6: sum(v on pair 1) = sum(v on pair 2)
    #                      = sum(v on pair 3)}.
    relation_rows = []
    for other_pair in pairs[1:]:
        row = [0] * 6
        for point in pairs[0]:
            row[point - 1] ^= 1
        for point in other_pair:
            row[point - 1] ^= 1
        relation_rows.append(row)
    code = Matrix(GF(2), relation_rows).right_kernel()
    if code.dimension() != 4:
        raise ArithmeticError("the pair-parity code does not have dimension four")

    generators = []
    # A4 in every quartic block.
    for block in range(6):
        points = [4 * block + offset + 1 for offset in range(4)]
        images = list(range(1, 25))
        images[points[0] - 1] = points[1]
        images[points[1] - 1] = points[2]
        images[points[2] - 1] = points[0]
        generators.append(perm_from_images(images))

        images = list(range(1, 25))
        images[points[0] - 1] = points[1]
        images[points[1] - 1] = points[0]
        images[points[2] - 1] = points[3]
        images[points[3] - 1] = points[2]
        generators.append(perm_from_images(images))

    # Representatives of every allowed base sign vector modulo A4^6.
    for vector in code.basis():
        images = list(range(1, 25))
        for block, bit in enumerate(vector):
            if bit:
                point = 4 * block + 1
                images[point - 1] = point + 1
                images[point] = point
        generators.append(perm_from_images(images))

    # Standard quotient lifts.  Since all of C and A4^6 are already present,
    # adjoining these lifts gives the full preimage C semidirect 6T11 and
    # absorbs any alternative lift/cocycle whose parity vector lies in C.
    for quotient_generator in list(libgap.GeneratorsOfGroup(quotient)):
        images = []
        for block in range(1, 7):
            target_block = image(block, quotient_generator)
            for offset in range(4):
                images.append(4 * (target_block - 1) + offset + 1)
        generators.append(perm_from_images(images))

    group = libgap.Group(generators)
    points24 = libgap.eval("[1..24]")
    expected_order = 12**6 * 2**4 * 48
    result = {
        "quotientLabel": "6T11",
        "quotientOrder": int(libgap.Size(quotient)),
        "oppositePairs": [list(pair) for pair in pairs],
        "parityCodeDimension": int(code.dimension()),
        "parityCodeBasis": [
            [int(value) for value in vector] for vector in code.basis()
        ],
        "kernelFormula": "A4^6 semidirect C2^4",
        "expectedOrder": expected_order,
        "actualOrder": int(libgap.Size(group)),
        "transitive": bool(libgap.IsTransitive(group, points24)),
        "transitiveNumber": int(libgap.TransitiveIdentification(group)),
        "fullPreimageNotChosenComplement": True,
        "cocycleObstruction": None,
    }
    if (
        result["actualOrder"] != expected_order
        or not result["transitive"]
        or result["transitiveNumber"] != TARGET_T
    ):
        raise ArithmeticError(f"sign-code group identification failed: {result}")
    return group, result


def generic_discriminant_identity():
    coefficient_ring = PolynomialRing(QQ, names=("k", "m", "y"))
    k, m, y = coefficient_ring.gens()
    quartic_ring = PolynomialRing(coefficient_ring, "X")
    X = quartic_ring.gen()
    quartic = X**4 + 4 * k * X**2 + 4 * m * X + y
    discriminant = coefficient_ring(quartic.discriminant())
    opposite_discriminant = discriminant(k=k, m=m, y=-y)
    ell = 4 * k * (9 * m**2 + 4 * k**3)
    em = m**2 * (27 * m**2 + 16 * k**3)
    claimed_product = 65536 * (
        (8 * k**2 * y**2 + em) ** 2 - y**2 * (y**2 + ell) ** 2
    )
    verified = discriminant * opposite_discriminant == claimed_product
    if not verified:
        raise ArithmeticError("generic paired-discriminant identity failed")
    return {
        "verified": True,
        "quartic": "X^4+4*k*X^2+4*m*X+y",
        "discriminant": str(discriminant),
        "pairedProduct": str(claimed_product),
        "consequenceAtRootOfQ": (
            "Disc(y)*Disc(-y)=D*(256*(A*y^2+B))^2"
        ),
    }


def maximal_profiles(target):
    points = libgap.eval("[1..24]")
    rows = []
    for subgroup in list(libgap.MaximalSubgroupClassReps(target)):
        if not bool(libgap.IsTransitive(subgroup, points)):
            continue
        profiles = {
            tuple(
                sorted(
                    int(length)
                    for length in libgap.CycleLengths(
                        libgap.Representative(conjugacy_class), points
                    )
                )
            )
            for conjugacy_class in list(libgap.ConjugacyClasses(subgroup))
        }
        rows.append(
            {
                "label": f"24T{int(libgap.TransitiveIdentification(subgroup))}",
                "order": int(libgap.Size(subgroup)),
                "profiles": profiles,
            }
        )
    if len(rows) != 9:
        raise ArithmeticError(f"expected nine transitive maximal classes, got {len(rows)}")
    return rows


def group_profiles(transitive_number):
    group = libgap.TransitiveGroup(24, transitive_number)
    points = libgap.eval("[1..24]")
    return {
        tuple(
            sorted(
                int(length)
                for length in libgap.CycleLengths(
                    libgap.Representative(conjugacy_class), points
                )
            )
        )
        for conjugacy_class in list(libgap.ConjugacyClasses(group))
    }


def live_gate(digest):
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    target = connection.execute(
        """
        SELECT team_count,discovered,generated_at
        FROM targets WHERE label=? AND r=?
        """,
        (TARGET_LABEL, TARGET_R),
    ).fetchone()
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
        (TARGET_LABEL, TARGET_R),
    ).fetchone()
    owned = connection.execute(
        """
        SELECT 1 FROM verifications
        WHERE label=? AND r=? AND scoreable=1
        """,
        (TARGET_LABEL, TARGET_R),
    ).fetchone()
    known_hash = connection.execute(
        "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
    ).fetchone()
    connection.close()
    return {
        "teamCount": None if target is None else int(target[0]),
        "discovered": None if target is None else int(target[1]),
        "generatedAt": None if target is None else target[2],
        "baseline": baseline is not None,
        "owned": owned is not None,
        "knownHash": known_hash is not None,
        "currentTc0": bool(
            target
            and int(target[0]) == 0
            and int(target[1]) == 0
            and baseline is None
            and owned is None
        ),
    }


def audit_row(row, maximal_rows, alternative_profiles):
    ring_z = PolynomialRing(QQ, "z")
    ring_y = PolynomialRing(QQ, "y")
    ring_x = PolynomialRing(QQ, "x")
    z = ring_z.gen()
    y = ring_y.gen()
    x = ring_x.gen()

    k = ZZ(row["k"])
    m = ZZ(row["m"])
    a_value = ZZ(row["A"])
    b_value = ZZ(row["B"])
    d_value = ZZ(row["D"])
    ell = 4 * k * (9 * m**2 + 4 * k**3)
    em = m**2 * (27 * m**2 + 16 * k**3)
    cubic = (
        z * (z + ell) ** 2
        - (8 * k**2 * z + em) ** 2
        + d_value * (a_value * z + b_value) ** 2
    )
    sextic = ring_y(cubic(y**2))
    quartic_map = x**4 + 4 * k * x**2 + 4 * m * x
    polynomial = ring_x(cubic(quartic_map**2))

    claimed_cubic = polynomial_from_line(ring_z, row["cubic"])
    claimed_sextic = polynomial_from_line(ring_y, row["sextic"])
    claimed_polynomial = polynomial_from_line(ring_x, row["polynomial"])
    if cubic != claimed_cubic or sextic != claimed_sextic:
        raise ArithmeticError("stored quotient reconstruction failed")
    if polynomial != claimed_polynomial:
        raise ArithmeticError("stored degree-24 reconstruction failed")
    line = coefficient_line(polynomial)
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != row["coefficientSha256"]:
        raise ArithmeticError("coefficient hash mismatch")
    if not cubic.is_irreducible() or int(cubic.galois_group().transitive_number()) != 2:
        raise ArithmeticError("cubic is not exact S3")
    if not sextic.is_irreducible() or int(sextic.galois_group().transitive_number()) != 11:
        raise ArithmeticError("sextic is not exact 6T11")
    if not polynomial.is_irreducible():
        raise ArithmeticError("degree-24 polynomial is reducible")
    integral_polynomial = polynomial.change_ring(ZZ)
    primitive = integral_polynomial.content() == 1
    if not primitive:
        raise ArithmeticError("degree-24 polynomial is not primitive")
    real_roots = int(polynomial.number_of_real_roots())
    if real_roots != TARGET_R:
        raise ArithmeticError(f"wrong real-root count: {real_roots}")

    observations = []
    for prime, claimed_profile in row["observedFrobenius"]:
        actual_profile = cycle_type(polynomial, int(prime))
        if actual_profile is None or actual_profile != tuple(claimed_profile):
            raise ArithmeticError(
                f"Frobenius reconstruction failed at p={prime}: "
                f"{actual_profile} != {claimed_profile}"
            )
        observations.append((int(prime), actual_profile))

    maximal_certificate = []
    for maximal in maximal_rows:
        witness = next(
            (
                {"prime": prime, "cycleType": list(profile)}
                for prime, profile in observations
                if profile not in maximal["profiles"]
            ),
            None,
        )
        maximal_certificate.append(
            {
                "label": maximal["label"],
                "order": maximal["order"],
                "witness": witness,
            }
        )
    complete = all(entry["witness"] is not None for entry in maximal_certificate)
    if not complete:
        raise ArithmeticError("a proper transitive maximal subgroup remains")
    alternative_witness = next(
        (
            {"prime": prime, "cycleType": list(profile)}
            for prime, profile in observations
            if profile not in alternative_profiles
        ),
        None,
    )
    if alternative_witness is None:
        raise ArithmeticError("the alternative cocycle/complement 24T24876 remains")

    return {
        "parameters": {
            "k": int(k),
            "m": int(m),
            "A": int(a_value),
            "B": int(b_value),
            "D": int(d_value),
        },
        "coefficientLine": line,
        "coefficientSha256": digest,
        "degree": int(polynomial.degree()),
        "primitive": primitive,
        "irreducible": True,
        "realRoots": real_roots,
        "quotient": {
            "coefficientLine": coefficient_line(sextic),
            "irreducible": True,
            "exactLabel": "6T11",
        },
        "maximalSubgroupCertificate": {
            "completeConditionalOnContainment": True,
            "properTransitiveMaximals": maximal_certificate,
        },
        "alternativeComplementExclusion": {
            "label": "24T24876",
            "sameOrderAsTarget": True,
            "witness": alternative_witness,
            "excluded": True,
        },
        "exactConclusion": TARGET_LABEL,
        "liveGate": live_gate(digest),
    }


def main():
    source_rows = [
        json.loads(line)
        for line in INPUT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(source_rows) != 4:
        raise ArithmeticError(f"expected four source rows, got {len(source_rows)}")

    sign_group, containment = sign_code_group()
    identity = generic_discriminant_identity()
    maxima = maximal_profiles(sign_group)
    alternative_profiles = group_profiles(ALTERNATIVE_COMPLEMENT_T)
    certificates = [
        audit_row(row, maxima, alternative_profiles) for row in source_rows
    ]
    if not all(
        row["maximalSubgroupCertificate"]["completeConditionalOnContainment"]
        and row["exactConclusion"] == TARGET_LABEL
        for row in certificates
    ):
        raise ArithmeticError("not all rows received exact certificates")

    payload = {
        "schemaVersion": "p27-s4-wreath-6t11-pairnorm-exact-v1",
        "status": "four_exact_24T24877_r14_certificates",
        "target": {"label": TARGET_LABEL, "t": TARGET_T, "r": TARGET_R},
        "genericDiscriminantIdentity": identity,
        "containmentGroup": containment,
        "containmentProof": (
            "For each opposite quotient pair {y,-y}, choose quartic "
            "Vandermonde square roots so their product is "
            "sqrt(D)*256*(A*y^2+B). Every absolute automorphism therefore "
            "has the same parity sum on all three opposite pairs. Hence its "
            "wreath sign vector lies in C. The explicitly constructed group "
            "is the full preimage A4^6.C over 6T11, so arbitrary quotient "
            "lifts and cocycles satisfying the relation are included. Its "
            "exact transitive identification is 24T24877."
        ),
        "cocycleAdversarialCheck": {
            "alternativeLabel": "24T24876",
            "alternativeOrder": int(
                libgap.Size(
                    libgap.TransitiveGroup(24, ALTERNATIVE_COMPLEMENT_T)
                )
            ),
            "alternativeDistinctCycleTypes": len(alternative_profiles),
            "resolution": (
                "Normalizing the six Vandermonde square roots pairwise puts "
                "the absolute action in the explicitly built full code "
                "preimage 24T24877, so no complement choice remains. Even "
                "under the weaker two-complement classification, every row "
                "has an unramified Frobenius type absent from 24T24876."
            ),
        },
        "maximalSubgroupLogic": (
            "Irreducibility makes the absolute group transitive. Containment "
            "puts it in 24T24877. A proper transitive subgroup lies in one of "
            "the nine transitive maximal subgroups. For every such maximal, "
            "an unramified factorization type absent from that maximal is "
            "recorded; therefore the absolute group equals 24T24877."
        ),
        "certificates": certificates,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_json(OUTPUT, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "output": str(OUTPUT),
                "hashes": [
                    row["coefficientSha256"] for row in certificates
                ],
                "allCurrentTc0": all(
                    row["liveGate"]["currentTc0"] for row in certificates
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
