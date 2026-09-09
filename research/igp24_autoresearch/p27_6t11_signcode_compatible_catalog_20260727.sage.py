#!/usr/bin/env sage
"""Exhaustive degree-24 catalog for the 6T11/two-sign-relation lane.

The six degree-four fibers carry their intrinsic permutation signs.  The
natural three pairs of the 6T11 quotient are the roots y,-y over each root of
the cubic in y^2.  The arithmetic identity says that the discriminant product
over the union of any two quotient pairs is a square.  Over the splitting
field of the quotient this forces two independent linear relations on the
six fiber-sign coordinates.

This script checks all 25,000 degree-24 transitive groups.  A row is retained
only if it has a 6-by-4 block system with quotient 6T11, order dividing the
maximal index-four S4 wreath order, and block-kernel sign code satisfying the
two relations.  Cycle profiles are stored for later Frobenius exclusion.
"""

from __future__ import annotations

import json
from pathlib import Path

from sage.all import GF, Matrix, libgap


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "p27_6t11_signcode_compatible_catalog_20260727.json"
POINTS24 = libgap.eval("[1..24]")
MAXIMAL_ORDER = (24**6 * 48) // 4


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def canonical_system(group, block) -> tuple[tuple[int, ...], ...]:
    return tuple(
        sorted(
            tuple(sorted(int(value) for value in item))
            for item in libgap.Orbit(group, block, libgap.OnSets)
        )
    )


def quotient_pairing(quotient) -> tuple[tuple[int, int], ...]:
    systems = set()
    for block in libgap.AllBlocks(quotient):
        if int(libgap.Size(block)) != 2:
            continue
        system = canonical_system(quotient, block)
        if len(system) == 3:
            systems.add(system)
    if len(systems) != 1:
        raise ArithmeticError(
            f"6T11 quotient has {len(systems)} pair systems, expected one"
        )
    return next(iter(systems))


def restricted_sign(permutation, block: tuple[int, ...]) -> int:
    position = {point: index + 1 for index, point in enumerate(block)}
    images = [
        position[int(libgap.OnPoints(point, permutation))]
        for point in block
    ]
    restriction = libgap.PermList(images)
    return int(int(libgap.SignPerm(restriction)) == -1)


def sign_code_data(kernel, blocks, pairing) -> dict:
    rows = [
        [restricted_sign(generator, block) for block in blocks]
        for generator in libgap.GeneratorsOfGroup(kernel)
    ]
    code = Matrix(GF(2), rows).row_space()
    relation_vectors = []
    for pair in pairing[1:]:
        support = set(pairing[0]) | set(pair)
        relation_vectors.append(
            [int(index + 1 in support) for index in range(6)]
        )
    relation_matrix = Matrix(GF(2), relation_vectors)
    relations_hold = all(
        relation_matrix * vector.column() == 0
        for vector in code.basis()
    )
    return {
        "dimension": int(code.dimension()),
        "basis": [[int(value) for value in vector] for vector in code.basis()],
        "pairing": [list(pair) for pair in pairing],
        "relationVectors": relation_vectors,
        "relationRank": int(relation_matrix.rank()),
        "relationsHold": relations_hold,
    }


def fiber_action(group, block):
    stabilizer = libgap.Stabilizer(group, libgap.Set(list(block)), libgap.OnSets)
    action = libgap.Action(stabilizer, list(block), libgap.OnPoints)
    return {
        "label": f"4T{int(libgap.TransitiveIdentification(action))}",
        "order": int(libgap.Size(action)),
    }


def cycle_type(permutation) -> tuple[int, ...]:
    return tuple(
        sorted(
            int(value)
            for value in libgap.CycleLengths(permutation, POINTS24)
        )
    )


def inspect_group(t: int) -> dict | None:
    group = libgap.TransitiveGroup(24, t)
    order = int(libgap.Size(group))
    if order % 48 != 0 or MAXIMAL_ORDER % order != 0:
        return None

    systems = []
    seen = set()
    for block in libgap.AllBlocks(group):
        if int(libgap.Size(block)) != 4:
            continue
        system = canonical_system(group, block)
        if len(system) != 6 or system in seen:
            continue
        seen.add(system)
        gap_system = libgap.AsSet(
            [libgap.Set(list(item)) for item in system]
        )
        homomorphism = libgap.ActionHomomorphism(
            group, gap_system, libgap.OnSets
        )
        quotient = libgap.Image(homomorphism)
        if (
            int(libgap.Size(quotient)) != 48
            or int(libgap.TransitiveIdentification(quotient)) != 11
        ):
            continue
        kernel = libgap.Kernel(homomorphism)
        pairing = quotient_pairing(quotient)
        sign_code = sign_code_data(kernel, system, pairing)
        if not sign_code["relationsHold"]:
            continue
        systems.append(
            {
                "blocks": [list(item) for item in system],
                "kernelOrder": int(libgap.Size(kernel)),
                "fiberAction": fiber_action(group, system[0]),
                "signCode": sign_code,
            }
        )

    if not systems:
        return None
    classes = list(libgap.ConjugacyClasses(group))
    profiles = sorted(
        {
            cycle_type(libgap.Representative(conjugacy_class))
            for conjugacy_class in classes
        }
    )
    possible_real_roots = sorted(
        {
            24
            - int(
                libgap.NrMovedPoints(
                    libgap.Representative(conjugacy_class)
                )
            )
            for conjugacy_class in classes
            if int(libgap.Order(libgap.Representative(conjugacy_class)))
            in (1, 2)
        }
    )
    return {
        "label": f"24T{t}",
        "t": t,
        "order": order,
        "indexInMaximalEnvelope": MAXIMAL_ORDER // order,
        "systems": systems,
        "cycleProfiles": [list(profile) for profile in profiles],
        "possibleRealRoots": possible_real_roots,
    }


def main() -> int:
    rows = []
    order_candidates = 0
    quotient_candidates = 0
    for t in range(1, 25001):
        group = libgap.TransitiveGroup(24, t)
        order = int(libgap.Size(group))
        if order % 48 == 0 and MAXIMAL_ORDER % order == 0:
            order_candidates += 1
            row = inspect_group(t)
            if row is not None:
                quotient_candidates += 1
                rows.append(row)
        if t % 1000 == 0:
            print(
                json.dumps(
                    {
                        "checked": t,
                        "orderCandidates": order_candidates,
                        "compatible": len(rows),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    payload = {
        "schemaVersion": "p27-6t11-signcode-compatible-catalog-v1",
        "degree24GroupsChecked": 25000,
        "exhaustiveDegree24": True,
        "quotientLabel": "6T11",
        "quotientOrder": 48,
        "fiberDegree": 4,
        "signRelationRank": 2,
        "maximalEnvelopeOrder": MAXIMAL_ORDER,
        "orderCandidates": order_candidates,
        "compatibleCount": len(rows),
        "targetIncluded": any(row["t"] == 24877 for row in rows),
        "maximalOrderLabels": [
            row["label"] for row in rows if row["order"] == MAXIMAL_ORDER
        ],
        "groups": rows,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(OUTPUT, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "compatibleCount": len(rows),
                "targetIncluded": payload["targetIncluded"],
                "maximalOrderLabels": payload["maximalOrderLabels"],
                "output": str(OUTPUT),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
