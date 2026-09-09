#!/usr/bin/env sage
"""Audit the cubic-resolvent and sign quotients of the rank-11 S4 targets.

For every current Alex-only degree-24 target with a 6-by-4 block system and
S4 fiber action, build the induced permutation action on the three perfect
matchings of each quartic block.  This is the global cubic-resolvent action.
The kernel is the product/submodule of the six V4 kernels; its exact size and
the induced degree-18 action tell us which arithmetic dependency must be
constructed before attempting a relative-quartic lift.

The script is offline and performs no staging or submission.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sage.all import GF, Matrix, libgap


ROOT = Path(__file__).resolve().parent
PLACEMENTS = ROOT / "data" / "current_rank11_alex_20260812_unique_placements.jsonl"
BLOCK_CENSUS = ROOT / "data" / "current_rank11_alex_block_census_20260814.jsonl"
OUTPUT = ROOT / "data" / "current_rank11_alex_s4_resolvent_actions_all_20260814.json"


def canonical_system(group, block):
    return tuple(
        sorted(
            tuple(sorted(int(value) for value in item))
            for item in libgap.Orbit(group, block, libgap.OnSets)
        )
    )


def image(point, permutation):
    return int(libgap.OnPoints(point, permutation))


def perfect_matchings(block):
    a, b, c, d = sorted(block)
    return (
        ((a, b), (c, d)),
        ((a, c), (b, d)),
        ((a, d), (b, c)),
    )


def map_matching(matching, permutation):
    return tuple(
        sorted(
            tuple(sorted(image(point, permutation) for point in pair))
            for pair in matching
        )
    )


def induced_group(group, objects, map_object):
    object_index = {obj: index + 1 for index, obj in enumerate(objects)}
    generators = []
    for generator in libgap.GeneratorsOfGroup(group):
        images = [object_index[map_object(obj, generator)] for obj in objects]
        generators.append(libgap.PermList(images))
    return libgap.Group(generators)


def restricted_sign(permutation, block):
    position = {point: index + 1 for index, point in enumerate(block)}
    images = [position[image(point, permutation)] for point in block]
    return int(int(libgap.SignPerm(libgap.PermList(images))) == -1)


def sign_code(kernel, blocks):
    rows = [
        [restricted_sign(generator, block) for block in blocks]
        for generator in libgap.GeneratorsOfGroup(kernel)
    ]
    code = Matrix(GF(2), rows).row_space()
    dual = Matrix(GF(2), list(code.basis())).right_kernel()
    return {
        "dimension": int(code.dimension()),
        "basis": [[int(value) for value in vector] for vector in code.basis()],
        "dualDimension": int(dual.dimension()),
        "dualBasis": [[int(value) for value in vector] for vector in dual.basis()],
    }


def factor_orders(group):
    orders = [int(libgap.Size(item)) for item in libgap.CompositionSeries(group)]
    return sorted(
        orders[index] // orders[index + 1]
        for index in range(len(orders) - 1)
    )


def cycle_type(permutation, degree):
    return tuple(
        sorted(
            int(value)
            for value in libgap.CycleLengths(
                permutation, libgap.eval(f"[1..{degree}]")
            )
        )
    )


def inspect(label, pair_count, expected_system_sha, expected_quotient_t, expected_fiber_t):
    t = int(label.split("T", 1)[1])
    group = libgap.TransitiveGroup(24, t)
    systems = []
    seen = set()
    for seed in libgap.AllBlocks(group):
        if int(libgap.Size(seed)) != 4:
            continue
        blocks = canonical_system(group, seed)
        if len(blocks) != 6 or blocks in seen:
            continue
        seen.add(blocks)
        gap_blocks = libgap.AsSet([libgap.Set(list(block)) for block in blocks])
        block_hom = libgap.ActionHomomorphism(group, gap_blocks, libgap.OnSets)
        quotient = libgap.Image(block_hom)
        block_kernel = libgap.Kernel(block_hom)
        stabilizer = libgap.Stabilizer(group, libgap.Set(list(blocks[0])), libgap.OnSets)
        fiber = libgap.Action(stabilizer, list(blocks[0]), libgap.OnPoints)
        quotient_t = int(libgap.TransitiveIdentification(quotient))
        if quotient_t != expected_quotient_t:
            continue
        if int(libgap.TransitiveIdentification(fiber)) != expected_fiber_t:
            continue

        matching_objects = tuple(
            matching for block in blocks for matching in perfect_matchings(block)
        )
        resolvent = induced_group(group, matching_objects, map_matching)
        resolvent_order = int(libgap.Size(resolvent))
        resolvent_transitive = bool(
            libgap.IsTransitive(resolvent, libgap.eval("[1..18]"))
        )
        resolvent_t = (
            int(libgap.TransitiveIdentification(resolvent))
            if resolvent_transitive
            else None
        )
        v4_kernel_order = int(libgap.Size(group)) // resolvent_order
        systems.append(
            {
                "blocks": [list(block) for block in blocks],
                "quotientLabel": f"6T{quotient_t}",
                "quotientOrder": int(libgap.Size(quotient)),
                "blockKernelOrder": int(libgap.Size(block_kernel)),
                "blockKernelDerivedOrder": int(
                    libgap.Size(libgap.DerivedSubgroup(block_kernel))
                ),
                "blockKernelCenterOrder": int(libgap.Size(libgap.Center(block_kernel))),
                "blockKernelExponent": int(libgap.Exponent(block_kernel)),
                "blockKernelCompositionFactorOrders": factor_orders(block_kernel),
                "blockKernelSignCode": sign_code(block_kernel, blocks),
                "resolventActionDegree": 18,
                "resolventActionOrder": resolvent_order,
                "resolventActionTransitive": resolvent_transitive,
                "resolventActionLabel": (
                    f"18T{resolvent_t}" if resolvent_t is not None else None
                ),
                "resolventActionCompositionFactorOrders": factor_orders(resolvent),
                "resolventCycleProfiles": [
                    list(profile)
                    for profile in sorted(
                        {
                            cycle_type(libgap.Representative(cls), 18)
                            for cls in libgap.ConjugacyClasses(resolvent)
                        }
                    )
                ],
                "quarticV4KernelOrder": v4_kernel_order,
                "quarticV4KernelIsFull": v4_kernel_order == 4**6,
                "resolventIndexOverBlockQuotient": (
                    resolvent_order // int(libgap.Size(quotient))
                ),
                "expectedSystemSha256": expected_system_sha,
            }
        )
    if len(systems) != 1:
        raise ArithmeticError(
            f"{label}: expected one 6T{expected_quotient_t}/4T{expected_fiber_t} system, got {len(systems)}"
        )
    points24 = libgap.eval("[1..24]")
    maximal_rows = []
    for subgroup in libgap.MaximalSubgroupClassReps(group):
        if not bool(libgap.IsTransitive(subgroup, points24)):
            continue
        maximal_rows.append(
            {
                "label": f"24T{int(libgap.TransitiveIdentification(subgroup))}",
                "order": int(libgap.Size(subgroup)),
                "cycleProfiles": [
                    list(profile)
                    for profile in sorted(
                        {
                            cycle_type(libgap.Representative(cls), 24)
                            for cls in libgap.ConjugacyClasses(subgroup)
                        }
                    )
                ],
            }
        )
    return {
        "label": label,
        "t": t,
        "pairCount": pair_count,
        "groupOrder": int(libgap.Size(group)),
        "groupCompositionFactorOrders": factor_orders(group),
        "cycleProfiles": [
            list(profile)
            for profile in sorted(
                {
                    cycle_type(libgap.Representative(cls), 24)
                    for cls in libgap.ConjugacyClasses(group)
                }
            )
        ],
        "possibleRealRoots": sorted(
            {
                24
                - int(libgap.NrMovedPoints(libgap.Representative(cls)))
                for cls in libgap.ConjugacyClasses(group)
                if int(libgap.Order(libgap.Representative(cls))) in (1, 2)
            }
        ),
        "properTransitiveMaximals": maximal_rows,
        "system": systems[0],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fiber-t", type=int, default=5, choices=(4, 5))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    placements = [
        json.loads(line) for line in PLACEMENTS.read_text().splitlines() if line.strip()
    ]
    pair_counts = Counter(str(row["label"]) for row in placements)
    census = [
        json.loads(line) for line in BLOCK_CENSUS.read_text().splitlines() if line.strip()
    ]
    targets = []
    for row in census:
        system = next(
            (
                item
                for item in row["blockSystems"]
                if item["shape"] == "6x4"
                and item["fiberActionLabel"] == f"4T{args.fiber_t}"
            ),
            None,
        )
        if system is None:
            continue
        targets.append(
            inspect(
                str(row["label"]),
                int(pair_counts[str(row["label"])]),
                str(system["systemSha256"]),
                int(str(system["quotientActionLabel"]).split("T", 1)[1]),
                args.fiber_t,
            )
        )
    targets.sort(key=lambda row: (-row["pairCount"], row["label"]))
    payload = {
        "schemaVersion": f"rank11-4t{args.fiber_t}-resolvent-actions-v1",
        "targetCount": len(targets),
        "pairCount": sum(row["pairCount"] for row in targets),
        "targets": targets,
        "resolventActionDistribution": dict(
            sorted(Counter(row["system"]["resolventActionLabel"] for row in targets).items())
        ),
        "resolventIndexDistribution": dict(
            sorted(
                Counter(
                    str(row["system"]["resolventIndexOverBlockQuotient"])
                    for row in targets
                ).items()
            )
        ),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "targetCount": payload["targetCount"],
                "pairCount": payload["pairCount"],
                "resolventActionDistribution": payload["resolventActionDistribution"],
                "resolventIndexDistribution": payload["resolventIndexDistribution"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
