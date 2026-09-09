#!/usr/bin/env sage -python
"""Finite group-theoretic decomposition of 24T24429.

No polynomial search, network operation, staging, or submission is performed.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from sage.all import GF, Matrix, libgap


TARGET_T = 24429
PAIRINGS = (
    frozenset((frozenset((1, 2)), frozenset((3, 4)))),
    frozenset((frozenset((1, 3)), frozenset((2, 4)))),
    frozenset((frozenset((1, 4)), frozenset((2, 3)))),
)


def canonical_system(group, block):
    return tuple(
        sorted(
            tuple(sorted(int(point) for point in member))
            for member in libgap.Orbit(group, block, libgap.OnSets)
        )
    )


def local_permutation(element, block):
    positions = {point: index + 1 for index, point in enumerate(block)}
    images = [
        positions[int(libgap.OnPoints(point, element))]
        for point in block
    ]
    return libgap.PermList(images)


def resolvent_permutation(local):
    images = []
    for pairing in PAIRINGS:
        moved = frozenset(
            frozenset(
                int(libgap.OnPoints(point, local))
                for point in pair
            )
            for pair in pairing
        )
        images.append(PAIRINGS.index(moved) + 1)
    return libgap.PermList(images)


def disjoint_resolvent_permutation(element, blocks):
    images = []
    for block_index, block in enumerate(blocks):
        local = resolvent_permutation(local_permutation(element, block))
        offset = 3 * block_index
        images.extend(
            offset + int(libgap.OnPoints(point, local))
            for point in range(1, 4)
        )
    return libgap.PermList(images)


def sign_vector(element, blocks):
    return [
        int(libgap.SignPerm(local_permutation(element, block))) == -1
        for block in blocks
    ]


def ternary_vector(element, block_count):
    output = []
    for block_index in range(block_count):
        offset = 3 * block_index
        images = tuple(
            int(libgap.OnPoints(offset + point, element)) - offset
            for point in range(1, 4)
        )
        if images == (1, 2, 3):
            output.append(0)
        elif images == (2, 3, 1):
            output.append(1)
        elif images == (3, 1, 2):
            output.append(2)
        else:
            raise ArithmeticError(
                f"derived resolvent element is not in C3: {images}"
            )
    return output


def code_summary(rows, prime, length):
    matrix = Matrix(GF(prime), rows)
    code = matrix.row_space()
    dual = Matrix(GF(prime), list(code.basis())).right_kernel()
    code_vectors = [tuple(int(value) for value in row) for row in code]
    dual_vectors = [tuple(int(value) for value in row) for row in dual]
    return {
        "basis": [
            [int(value) for value in row] for row in code.basis()
        ],
        "dimension": int(code.dimension()),
        "dualBasis": [
            [int(value) for value in row] for row in dual.basis()
        ],
        "dualDimension": int(dual.dimension()),
        "dualNonzeroVectors": [
            list(row) for row in dual_vectors if any(row)
        ],
        "length": length,
        "prime": prime,
        "weightEnumerator": {
            str(weight): count
            for weight, count in sorted(
                Counter(
                    sum(value != 0 for value in row)
                    for row in code_vectors
                ).items()
            )
        },
    }


def cycle_type(permutation, degree):
    lengths = [
        int(value)
        for value in libgap.CycleLengths(
            permutation, libgap.eval(f"[1..{degree}]")
        )
    ]
    return sorted(lengths)


def main():
    group = libgap.TransitiveGroup(24, TARGET_T)
    minimal_block = next(
        block
        for block in libgap.AllBlocks(group)
        if int(libgap.Length(block)) == 4
    )
    blocks = canonical_system(group, minimal_block)
    gap_blocks = libgap.AsSet(
        [libgap.Set(list(block)) for block in blocks]
    )
    quotient_map = libgap.ActionHomomorphism(
        group, gap_blocks, libgap.OnSets
    )
    quotient = libgap.Image(quotient_map)
    kernel = libgap.Kernel(quotient_map)
    kernel_generators = list(libgap.GeneratorsOfGroup(kernel))

    resolvent_generator_images = [
        disjoint_resolvent_permutation(element, blocks)
        for element in kernel_generators
    ]
    resolvent_image = libgap.Group(resolvent_generator_images)
    resolvent_map = libgap.GroupHomomorphismByImages(
        kernel,
        resolvent_image,
        libgap.GeneratorsOfGroup(kernel),
        resolvent_generator_images,
    )
    if resolvent_map == libgap.fail:
        raise ArithmeticError("failed to construct the six-fiber resolvent map")
    v4_layer = libgap.Kernel(resolvent_map)

    sign_code = code_summary(
        [
            [int(value) for value in sign_vector(element, blocks)]
            for element in kernel_generators
        ],
        2,
        6,
    )
    cubic_subgroup = libgap.DerivedSubgroup(resolvent_image)
    cubic_code = code_summary(
        [
            ternary_vector(element, 6)
            for element in libgap.GeneratorsOfGroup(cubic_subgroup)
        ],
        3,
        6,
    )

    local_fibers = []
    local_resolvents = []
    for block_index, block in enumerate(blocks):
        fiber = libgap.Group(
            [
                local_permutation(element, block)
                for element in kernel_generators
            ]
        )
        local_fibers.append(
            {
                "block": block_index + 1,
                "order": int(libgap.Size(fiber)),
                "transitiveLabel": (
                    f"4T{int(libgap.TransitiveIdentification(fiber))}"
                ),
            }
        )
        offset = 3 * block_index
        local = libgap.Group(
            [
                libgap.PermList(
                    [
                        int(libgap.OnPoints(offset + point, element))
                        - offset
                        for point in range(1, 4)
                    ]
                )
                for element in resolvent_generator_images
            ]
        )
        local_resolvents.append(
            {
                "block": block_index + 1,
                "order": int(libgap.Size(local)),
                "transitiveLabel": (
                    f"3T{int(libgap.TransitiveIdentification(local))}"
                ),
            }
        )

    quotient_blocks = []
    seen_quotient_systems = set()
    for block in libgap.AllBlocks(quotient):
        size = int(libgap.Length(block))
        if size in (1, 6):
            continue
        system = canonical_system(quotient, block)
        if system in seen_quotient_systems:
            continue
        seen_quotient_systems.add(system)
        quotient_blocks.append([list(member) for member in system])
    quotient_blocks.sort()
    if quotient_blocks != [[[1, 2, 3], [4, 5, 6]]]:
        raise ArithmeticError(
            f"unexpected 6T10 block system: {quotient_blocks}"
        )

    first_half_points = [
        point for block in blocks[:3] for point in block
    ]
    second_half_points = [
        point for block in blocks[3:] for point in block
    ]
    first_half_kernel = libgap.Stabilizer(
        kernel, second_half_points, libgap.OnTuples
    )
    second_half_kernel = libgap.Stabilizer(
        kernel, first_half_points, libgap.OnTuples
    )
    half_product = libgap.Group(
        list(libgap.GeneratorsOfGroup(first_half_kernel))
        + list(libgap.GeneratorsOfGroup(second_half_kernel))
    )

    derived1 = libgap.DerivedSubgroup(kernel)
    derived2 = libgap.DerivedSubgroup(derived1)
    derived3 = libgap.DerivedSubgroup(derived2)
    complements = libgap.ComplementClassesRepresentatives(group, kernel)

    involutions = []
    coverage = defaultdict(set)
    for conjugacy_class in libgap.ConjugacyClasses(group):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        quotient_element = libgap.Image(quotient_map, representative)
        quotient_fixed = cycle_type(quotient_element, 6).count(1)
        target_fixed = cycle_type(representative, 24).count(1)
        row = {
            "classSize": int(libgap.Size(conjugacy_class)),
            "quotientCycleType": cycle_type(quotient_element, 6),
            "quotientFixedEmbeddings": quotient_fixed,
            "targetCycleType": cycle_type(representative, 24),
            "targetRealRoots": target_fixed,
        }
        involutions.append(row)
        coverage[quotient_fixed].add(target_fixed)
    involutions.sort(
        key=lambda row: (
            row["quotientFixedEmbeddings"],
            row["targetRealRoots"],
            row["targetCycleType"],
        )
    )

    result = {
        "ambient": {
            "fiberGroup": "S4",
            "fiberWreathOrder": 24**6 * int(libgap.Size(quotient)),
            "indexOfTargetInWreath": (
                24**6 * int(libgap.Size(quotient))
                // int(libgap.Size(group))
            ),
        },
        "blockSystem": {
            "blocks": [list(block) for block in blocks],
            "shape": "6x4",
        },
        "involutionLiftTable": involutions,
        "kernel": {
            "abelianInvariants": [
                int(value) for value in libgap.AbelianInvariants(kernel)
            ],
            "derivedOrders": [
                int(libgap.Size(value))
                for value in (kernel, derived1, derived2, derived3)
            ],
            "exponent": int(libgap.Exponent(kernel)),
            "localFiberImages": local_fibers,
            "order": int(libgap.Size(kernel)),
            "structureDescription": str(
                libgap.StructureDescription(kernel)
            ),
        },
        "layers": {
            "cubicResolvent": {
                "code": cubic_code,
                "imageAbelianInvariants": [
                    int(value)
                    for value in libgap.AbelianInvariants(
                        resolvent_image
                    )
                ],
                "imageDerivedOrder": int(libgap.Size(cubic_subgroup)),
                "imageOrder": int(libgap.Size(resolvent_image)),
                "localImages": local_resolvents,
                "structureDescription": str(
                    libgap.StructureDescription(resolvent_image)
                ),
            },
            "sign": sign_code,
            "v4": {
                "dimensionOverF2": int(libgap.Log2Int(libgap.Size(v4_layer))),
                "elementaryAbelian": bool(
                    libgap.IsElementaryAbelian(v4_layer)
                ),
                "equalsSecondDerived": bool(v4_layer == derived2),
                "order": int(libgap.Size(v4_layer)),
            },
        },
        "quotient": {
            "abelianInvariants": [
                int(value)
                for value in libgap.AbelianInvariants(quotient)
            ],
            "blocks": quotient_blocks,
            "derivedOrder": int(
                libgap.Size(libgap.DerivedSubgroup(quotient))
            ),
            "idGroup": [
                int(value) for value in libgap.IdGroup(quotient)
            ],
            "label": (
                f"6T{int(libgap.TransitiveIdentification(quotient))}"
            ),
            "order": int(libgap.Size(quotient)),
            "structureDescription": str(
                libgap.StructureDescription(quotient)
            ),
        },
        "signatureCoverageByQuotientRealRoots": {
            str(key): sorted(values)
            for key, values in sorted(coverage.items())
        },
        "threeFiberFactors": {
            "commutatorOrder": int(
                libgap.Size(
                    libgap.CommutatorSubgroup(
                        first_half_kernel, second_half_kernel
                    )
                )
            ),
            "firstOrder": int(libgap.Size(first_half_kernel)),
            "firstStructure": str(
                libgap.StructureDescription(first_half_kernel)
            ),
            "generatedProductEqualsKernel": bool(half_product == kernel),
            "intersectionOrder": int(
                libgap.Size(
                    libgap.Intersection(
                        first_half_kernel, second_half_kernel
                    )
                )
            ),
            "secondOrder": int(libgap.Size(second_half_kernel)),
            "secondStructure": str(
                libgap.StructureDescription(second_half_kernel)
            ),
        },
        "target": {
            "blockExtensionComplementClassCount": int(
                libgap.Length(complements)
            ),
            "blockExtensionIsSplit": bool(libgap.Length(complements) > 0),
            "label": "24T24429",
            "order": int(libgap.Size(group)),
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
