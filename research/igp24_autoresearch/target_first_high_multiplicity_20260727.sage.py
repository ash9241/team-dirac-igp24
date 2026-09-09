#!/usr/bin/env sage
"""Exact target-first structure census for ten high-multiplicity tc0 groups."""

from __future__ import annotations

import glob
import hashlib
import json
import sqlite3
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

from sage.all import GF, Matrix, VectorSpace, libgap


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "target_first_high_multiplicity_20260727.json"
TARGETS = [24429, 15051, 15091, 10381, 15082, 17170, 12926, 12936, 15398, 15519]


def canonical_system(group, block):
    return tuple(
        sorted(
            tuple(sorted(int(x) for x in item))
            for item in list(libgap.Orbit(group, block, libgap.OnSets))
        )
    )


def action_label(group, degree):
    return f"{degree}T{int(libgap.TransitiveIdentification(group))}"


def quotient_block_shapes(quotient, degree):
    rows, seen = [], set()
    for block in list(libgap.AllBlocks(quotient)):
        size = int(libgap.Length(block))
        if size <= 1 or size >= degree:
            continue
        system = canonical_system(quotient, block)
        if system in seen:
            continue
        seen.add(system)
        hom = libgap.ActionHomomorphism(
            quotient, libgap.AsSet([libgap.Set(list(x)) for x in system]), libgap.OnSets
        )
        image = libgap.Image(hom)
        rows.append(
            {
                "shape": f"{len(system)}x{size}",
                "blockSize": size,
                "blockCount": len(system),
                "quotientLabel": action_label(image, len(system)),
                "quotientOrder": int(libgap.Size(image)),
                "kernelOrder": int(libgap.Size(libgap.Kernel(hom))),
                "blocks": [list(x) for x in system],
            }
        )
    return sorted(rows, key=lambda x: (x["blockSize"], x["quotientLabel"], x["blocks"]))


def binary_kernel_code(kernel, blocks):
    rows = []
    for generator in list(libgap.GeneratorsOfGroup(kernel)):
        rows.append(
            [
                int(libgap.OnPoints(block[0], generator)) == block[1]
                for block in blocks
            ]
        )
    matrix = Matrix(GF(2), [[int(x) for x in row] for row in rows])
    code = matrix.row_space()
    dual = Matrix(GF(2), list(code.basis())).right_kernel()

    def vectors(space):
        return sorted(
            ([int(x) for x in vector] for vector in space),
            key=lambda x: (sum(x), x),
        )

    code_vectors = vectors(code)
    dual_vectors = vectors(dual)
    return {
        "dimension": int(code.dimension()),
        "basis": [[int(x) for x in row] for row in code.basis()],
        "weightEnumerator": dict(
            sorted(Counter(sum(row) for row in code_vectors).items())
        ),
        "dualDimension": int(dual.dimension()),
        "dualBasis": [[int(x) for x in row] for row in dual.basis()],
        "dualNonzeroSupports": [
            [index + 1 for index, value in enumerate(row) if value]
            for row in dual_vectors
            if any(row)
        ],
        "dualWeightEnumerator": dict(
            sorted(Counter(sum(row) for row in dual_vectors).items())
        ),
    }


@lru_cache(maxsize=None)
def recovered_quotient_evidence(quotient_t):
    candidates = []
    for path_string in glob.glob(
        str(ROOT / "data" / "agent_f5_full_ledger_pair_product_routes_shard*of4.jsonl")
    ):
        path = Path(path_string)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if int(row.get("action", {}).get("quotientT12", -1)) != quotient_t:
                continue
            source = row.get("source", {})
            quotient_line = source.get("quotientLine")
            if not quotient_line:
                continue
            candidates.append(
                {
                    "quotientLine": quotient_line,
                    "quotientPolynomialSha256": source.get("quotientPolynomialSha256"),
                    "sourceLabel": source.get("label"),
                    "sourceR": source.get("r"),
                    "submissionId": source.get("submissionId"),
                    "polynomialIndex": source.get("polynomialIndex"),
                    "artifact": str(path.relative_to(ROOT)),
                    "coefficientBytes": len(quotient_line.encode("ascii")),
                }
            )
    if not candidates:
        return {"recovered": False, "witnessCount": 0}
    candidates.sort(
        key=lambda x: (
            x["coefficientBytes"],
            x["quotientPolynomialSha256"] or "",
            x["sourceLabel"] or "",
        )
    )
    return {
        "recovered": True,
        "witnessCount": len(candidates),
        "lowestCoefficientWitness": candidates[0],
    }


def main():
    started = time.monotonic()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    rows = []
    for target_t in TARGETS:
        group = libgap.TransitiveGroup(24, target_t)
        order = int(libgap.Size(group))
        systems = []
        seen = set()
        for block in list(libgap.AllBlocks(group)):
            size = int(libgap.Length(block))
            if size <= 1 or size >= 24:
                continue
            canonical = canonical_system(group, block)
            if canonical in seen:
                continue
            seen.add(canonical)
            gap_blocks = libgap.AsSet([libgap.Set(list(x)) for x in canonical])
            hom = libgap.ActionHomomorphism(group, gap_blocks, libgap.OnSets)
            quotient = libgap.Image(hom)
            kernel = libgap.Kernel(hom)
            elementary = bool(libgap.IsElementaryAbelian(kernel))
            system = {
                "shape": f"{len(canonical)}x{size}",
                "blockSize": size,
                "blockCount": len(canonical),
                "blocks": [list(x) for x in canonical],
                "quotientLabel": action_label(quotient, len(canonical)),
                "quotientOrder": int(libgap.Size(quotient)),
                "quotientBlockSystems": quotient_block_shapes(quotient, len(canonical)),
                "kernelOrder": int(libgap.Size(kernel)),
                "kernelIsAbelian": bool(libgap.IsAbelian(kernel)),
                "kernelIsElementaryAbelian": elementary,
                "kernelExponent": int(libgap.Exponent(kernel)),
                "kernelAbelianInvariants": sorted(
                    int(x) for x in libgap.AbelianInvariants(kernel)
                ),
                "kernelDerivedOrder": int(libgap.Size(libgap.DerivedSubgroup(kernel))),
                "kernelCenterOrder": int(libgap.Size(libgap.Center(kernel))),
                "kernelStructure": (
                    f"C2^{int(libgap.Size(kernel)).bit_length() - 1}"
                    if elementary
                    else "nonabelian; see exact order/exponent/derived/center invariants"
                ),
            }
            if size == 2 and system["kernelIsElementaryAbelian"]:
                system["binaryKummerCode"] = binary_kernel_code(
                    kernel, [list(x) for x in canonical]
                )
                system["extensionComplementClassCount"] = int(
                    libgap.Length(libgap.ComplementClassesRepresentatives(group, kernel))
                )
            systems.append(system)
        systems.sort(key=lambda x: (x["blockSize"], x["quotientLabel"], x["blocks"]))
        minimum = min(x["blockSize"] for x in systems)
        minimal_systems = [x for x in systems if x["blockSize"] == minimum]
        possible_r = sorted(
            {
                24 - int(libgap.NrMovedPoints(libgap.Representative(cls)))
                for cls in list(libgap.ConjugacyClasses(group))
                if int(libgap.Order(libgap.Representative(cls))) in (1, 2)
            }
        )
        tc0 = [
            int(r[0])
            for r in connection.execute(
                """
                SELECT r FROM targets
                WHERE label=? AND team_count=0 AND discovered=0
                ORDER BY r
                """,
                (f"24T{target_t}",),
            )
        ]
        for system in minimal_systems:
            qlabel = system["quotientLabel"]
            if qlabel.startswith("12T"):
                system["recoveredQuotientEvidence"] = recovered_quotient_evidence(
                    int(qlabel.split("T", 1)[1])
                )
        rows.append(
            {
                "label": f"24T{target_t}",
                "order": order,
                "isSolvable": bool(libgap.IsSolvableGroup(group)),
                "minimalBlockSize": minimum,
                "minimalBlockSystemCount": len(minimal_systems),
                "minimalBlockSystems": minimal_systems,
                "allBlockSystems": systems,
                "possibleRealRootSignatures": possible_r,
                "currentTc0Signatures": tc0,
                "attainableTc0Signatures": sorted(set(possible_r) & set(tc0)),
            }
        )
    payload = {
        "schemaVersion": "target-first-high-multiplicity-structure-v1",
        "elapsedSeconds": time.monotonic() - started,
        "targets": rows,
        "constructionFamilies": [
            {
                "family": "K4_edge_cycle_kummer_rank9",
                "targets": [
                    "24T15051",
                    "24T15082",
                    "24T15091",
                    "24T10381",
                    "24T17170",
                ],
                "formula": "P_{q,a}(x)=Res_y(q(y),x^2-a(y))",
                "kernel": (
                    "C2^9 inside C2^12.  Pair the 12 quotient roots into "
                    "the six edges of K4.  The six pair-parity coordinates "
                    "must lie in the binary cycle space Z_1(K4), while the "
                    "six within-pair coordinates are free."
                ),
                "dualInvariant": (
                    "For each of the four K4 vertices, the product of the "
                    "three edge-pair relative norms N_{K/M}(a) incident to "
                    "that vertex is a square.  The four star relations have "
                    "rank three."
                ),
                "arithmeticSearch": (
                    "Use the recovered q for 12T66, 12T59, or 12T111; "
                    "compute its degree-6 edge field M and degree-4 vertex "
                    "resolvent, solve the displayed star norm-square "
                    "condition there, then require exact squareclass span 9."
                ),
                "proofCaveat": (
                    "Every target extension is nonsplit, and 24T15051 and "
                    "24T15082 are distinct nonsplit extension classes with "
                    "the same 12T66 module.  Module rank alone is not an "
                    "exact-label proof; certify the extension class by exact "
                    "target containment plus maximal-subgroup Frobenius "
                    "exclusion."
                ),
            },
            {
                "family": "K4_even_cycle_kummer_rank8",
                "targets": ["24T12926", "24T12936", "24T15398"],
                "formula": "P_{q,a}(x)=Res_y(q(y),x^2-a(y))",
                "kernel": (
                    "C2^8.  The six edge-pair parities lie in "
                    "Z_1(K4) intersect the even-weight hyperplane; "
                    "equivalently impose the four K4 star relations plus "
                    "the total-edge parity relation."
                ),
                "arithmeticSearch": (
                    "Recovered quotient witnesses exist for 12T61, 12T59, "
                    "and 12T115.  Solve the star norm-square constraints and "
                    "one additional global norm-square constraint; require "
                    "exact squareclass span 8."
                ),
                "proofCaveat": "All three exact extensions are nonsplit.",
            },
            {
                "family": "six_edge_equal_parity_kummer_rank7",
                "targets": ["24T15519"],
                "formula": "P_{q,a}(x)=Res_y(q(y),x^2-a(y))",
                "kernel": (
                    "C2^7.  The parities of a on the two roots over each "
                    "of the six edge blocks are all equal.  Equivalently, "
                    "every product of two edge-pair relative norms is a "
                    "square (five independent relations)."
                ),
                "arithmeticSearch": (
                    "A recovered 12T135 quotient is pinned in the artifact. "
                    "Solve five relative norm-square relations and require "
                    "exact squareclass span 7."
                ),
                "proofCaveat": "The exact extension is nonsplit.",
            },
            {
                "family": "nonabelian_S4_fiber_subdirect",
                "targets": ["24T24429"],
                "obstruction": (
                    "The minimal blocks have size 4, quotient 6T10, and "
                    "nonabelian kernel order 2^14*3^4, exponent 12, trivial "
                    "center, and derived order 2^12*3^4.  It is not a "
                    "quadratic Kummer module and cannot arise from any "
                    "single radical lift over the recovered degree-12 "
                    "fields.  A relative quartic construction would need "
                    "simultaneous S4-discriminant and cubic-resolvent "
                    "subdirect relations (index 144 inside S4^6); no such "
                    "recovered invariant or source is present."
                ),
            },
        ],
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(OUTPUT)
    connection.close()
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "elapsedSeconds": payload["elapsedSeconds"],
                "targets": [
                    {
                        "label": row["label"],
                        "order": row["order"],
                        "minimalShape": [
                            x["shape"] for x in row["minimalBlockSystems"]
                        ],
                        "minimalQuotient": [
                            x["quotientLabel"] for x in row["minimalBlockSystems"]
                        ],
                        "attainableTc0Count": len(row["attainableTc0Signatures"]),
                    }
                    for row in rows
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
