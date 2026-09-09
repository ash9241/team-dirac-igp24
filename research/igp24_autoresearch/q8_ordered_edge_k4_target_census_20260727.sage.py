#!/usr/bin/env sage
"""Exact target census for the ordered-edge S4 K4-star Kummer family."""

import json
import sqlite3
from collections import Counter
from pathlib import Path

from sage.all import GF, Matrix, libgap


ROOT = Path.cwd()
DB = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "q8_ordered_edge_k4_target_census_20260727.json"
TARGETS = [10285, 10321, 10375, 10379, 10380, 10385, 10386]


def canonical_system(group, block):
    return tuple(
        sorted(
            tuple(sorted(int(x) for x in item))
            for item in libgap.Orbit(group, block, libgap.OnSets)
        )
    )


def code_data(kernel, blocks):
    rows = []
    for generator in libgap.GeneratorsOfGroup(kernel):
        rows.append(
            [
                int(libgap.OnPoints(block[0], generator)) == block[1]
                for block in blocks
            ]
        )
    code = Matrix(GF(2), rows).row_space()
    dual = Matrix(GF(2), list(code.basis())).right_kernel()
    dual_vectors = [tuple(int(x) for x in vector) for vector in dual]
    weight_six = [
        {i for i, value in enumerate(vector) if value}
        for vector in dual_vectors
        if sum(vector) == 6
    ]
    return {
        "dimension": int(code.dimension()),
        "dualDimension": int(dual.dimension()),
        "dualWeightEnumerator": dict(
            sorted(Counter(sum(vector) for vector in dual_vectors).items())
        ),
        "weightSixCount": len(weight_six),
        "weightSixPairIntersections": sorted(
            len(weight_six[i] & weight_six[j])
            for i in range(len(weight_six))
            for j in range(i + 1, len(weight_six))
        ),
        "coordinateStarMultiplicities": sorted(
            sum(index in support for support in weight_six)
            for index in range(12)
        ),
        "basis": [[int(x) for x in vector] for vector in code.basis()],
    }


def main():
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    rows = []
    for target_t in TARGETS:
        group = libgap.TransitiveGroup(24, target_t)
        selected = None
        for block in libgap.AllBlocks(group):
            if int(libgap.Size(block)) != 2:
                continue
            system = canonical_system(group, block)
            if len(system) != 12:
                continue
            gap_system = libgap.AsSet(
                [libgap.Set(list(pair)) for pair in system]
            )
            homomorphism = libgap.ActionHomomorphism(
                group, gap_system, libgap.OnSets
            )
            quotient = libgap.Image(homomorphism)
            if int(libgap.TransitiveIdentification(quotient)) != 8:
                continue
            kernel = libgap.Kernel(homomorphism)
            selected = (system, quotient, kernel)
            break
        if selected is None:
            raise ValueError(f"24T{target_t} lacks its 12T8 system")
        system, quotient, kernel = selected
        code = code_data(kernel, [list(pair) for pair in system])
        full_flip_images = list(range(1, 25))
        for first, second in system:
            full_flip_images[first - 1] = second
            full_flip_images[second - 1] = first
        full_flip = libgap.PermList(full_flip_images)
        possible_r = sorted(
            {
                24 - int(libgap.NrMovedPoints(libgap.Representative(cls)))
                for cls in libgap.ConjugacyClasses(group)
                if int(libgap.Order(libgap.Representative(cls))) in (1, 2)
            }
        )
        tc0 = [
            int(row[0])
            for row in connection.execute(
                """
                SELECT r FROM targets
                WHERE label=? AND team_count=0 AND discovered=0
                  AND NOT EXISTS (
                    SELECT 1 FROM baseline_pairs b
                    WHERE b.label=targets.label AND b.r=targets.r
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM verifications v
                    WHERE v.label=targets.label AND v.r=targets.r
                      AND v.scoreable=1
                  )
                ORDER BY r
                """,
                (f"24T{target_t}",),
            )
        ]
        rows.append(
            {
                "label": f"24T{target_t}",
                "order": int(libgap.Size(group)),
                "quotientOrder": int(libgap.Size(quotient)),
                "kernelOrder": int(libgap.Size(kernel)),
                "code": code,
                "isK4StarCode": (
                    code["dimension"] == 9
                    and code["dualDimension"] == 3
                    and code["dualWeightEnumerator"] == {0: 1, 6: 4, 8: 3}
                    and code["weightSixPairIntersections"] == [2] * 6
                    and code["coordinateStarMultiplicities"] == [2] * 12
                ),
                "fullFlipInGroup": bool(full_flip in group),
                "complementClassCount": int(
                    libgap.Length(
                        libgap.ComplementClassesRepresentatives(group, kernel)
                    )
                ),
                "possibleRealRoots": possible_r,
                "currentTc0": tc0,
                "attainableTc0": sorted(set(possible_r) & set(tc0)),
            }
        )
    connection.close()

    # Construct the split K4-star action directly.
    s4 = libgap.SymmetricGroup(4)
    edges = [
        (first, second)
        for first in range(1, 5)
        for second in range(1, 5)
        if first != second
    ]
    edge_position = {edge: index for index, edge in enumerate(edges)}
    quotient_generators = []
    for generator in libgap.GeneratorsOfGroup(s4):
        images = []
        for first, second in edges:
            image_edge = (
                int(libgap.OnPoints(first, generator)),
                int(libgap.OnPoints(second, generator)),
            )
            target = edge_position[image_edge]
            images.extend([2 * target + 1, 2 * target + 2])
        quotient_generators.append(libgap.PermList(images))
    star_rows = []
    for vertex in range(1, 5):
        star_rows.append(
            [
                int(first == vertex or second == vertex)
                for first, second in edges
            ]
        )
    code = Matrix(GF(2), star_rows).right_kernel()
    kernel_generators = []
    for vector in code.basis():
        images = list(range(1, 25))
        for index, value in enumerate(vector):
            if value:
                images[2 * index] = 2 * index + 2
                images[2 * index + 1] = 2 * index + 1
        kernel_generators.append(libgap.PermList(images))
    split_group = libgap.Group(quotient_generators + kernel_generators)
    split_t = int(libgap.TransitiveIdentification(split_group))
    payload = {
        "schemaVersion": "q8-ordered-edge-k4-target-census-v1",
        "splitActionLabel": f"24T{split_t}",
        "splitActionOrder": int(libgap.Size(split_group)),
        "targets": rows,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(OUTPUT)
    print(
        json.dumps(
            {
                "splitActionLabel": payload["splitActionLabel"],
                "targets": [
                    {
                        "label": row["label"],
                        "isK4StarCode": row["isK4StarCode"],
                        "complements": row["complementClassCount"],
                        "tc0": row["attainableTc0"],
                    }
                    for row in rows
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
