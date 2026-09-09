#!/usr/bin/env sage -python
"""Exact structural census for genuinely rank-raising scalar twists.

For each saved signed two-block action with quotient 12T59, 12T61, 12T115,
or 12T135, construct every length-12 orbit of odd subsets.  Its induced
24-point signed action H is retained only when the natural global flip is not
already in H and <H,flip> is one of the five requested target actions.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter, deque
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
ACTION_MAP = ROOT / "data" / "agent_gold_b_even_twist_action_map.jsonl"
OUTPUT = ROOT / "data" / "scalar_twist_rank_raise_census_20260727.json"
WANTED = {
    59: {12936, 15091},
    61: {12926},
    115: {15398},
    135: {15519},
}
EXPECTED_LOWER_ORDERS = {
    59: {24576, 12288},
    61: {12288},
    115: {24576},
    135: {24576},
}
ODD_SUBSET_SIZES = (1, 3, 5, 7, 9, 11)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def block_action_data(blocks, permutation):
    point_to_block = {}
    point_sign = {}
    for index, block in enumerate(blocks):
        point_to_block[block[0]] = index
        point_to_block[block[1]] = index
        point_sign[block[0]] = 0
        point_sign[block[1]] = 1
    block_permutation = []
    sign_vector = []
    for block in blocks:
        image = int(libgap.OnPoints(block[0], permutation))
        block_permutation.append(point_to_block[image])
        sign_vector.append(point_sign[image])
    return block_permutation, sign_vector


def subset_orbits(block_generators, subset_size):
    remaining = set(itertools.combinations(range(12), subset_size))
    result = []
    while remaining:
        start = min(remaining)
        orbit = {start}
        queue = deque([start])
        while queue:
            subset = queue.popleft()
            for permutation in block_generators:
                image = tuple(sorted(permutation[index] for index in subset))
                if image not in orbit:
                    orbit.add(image)
                    queue.append(image)
        remaining.difference_update(orbit)
        result.append(sorted(orbit))
    return result


def induced_permutation(orbit, block_permutation, sign_vector):
    position = {subset: index for index, subset in enumerate(orbit)}
    images = []
    for subset in orbit:
        image_subset = tuple(sorted(block_permutation[index] for index in subset))
        target = position[image_subset]
        sign = sum(sign_vector[index] for index in subset) % 2
        images.extend((2 * target + 1 + sign, 2 * target + 2 - sign))
    return libgap.PermList(images)


def orbit_permutation(orbit, block_permutation):
    position = {subset: index for index, subset in enumerate(orbit)}
    return libgap.PermList(
        [
            position[tuple(sorted(block_permutation[index] for index in subset))] + 1
            for subset in orbit
        ]
    )


def main() -> int:
    if OUTPUT.exists():
        raise ValueError(f"refusing to overwrite {OUTPUT}")
    rows = [json.loads(line) for line in ACTION_MAP.read_text().splitlines() if line.strip()]
    full_flip = libgap.PermList(
        [
            point + 1 if point % 2 else point - 1
            for point in range(1, 25)
        ]
    )
    survivors = []
    counters = Counter()

    for source in rows:
        relevant_systems = [
            (index, system)
            for index, system in enumerate(source["systems"])
            if int(system["blockActionT12"]) in WANTED
        ]
        if not relevant_systems:
            continue
        source_group = libgap.TransitiveGroup(24, int(source["sourceT"]))
        source_generators = list(libgap.GeneratorsOfGroup(source_group))
        for system_index, system in relevant_systems:
            quotient_t = int(system["blockActionT12"])
            blocks = [
                tuple(sorted(int(value) for value in block))
                for block in system["blocks"]
            ]
            generator_data = [
                block_action_data(blocks, generator)
                for generator in source_generators
            ]
            block_generators = [row[0] for row in generator_data]
            counters["systems"] += 1
            for subset_size in ODD_SUBSET_SIZES:
                for orbit in subset_orbits(block_generators, subset_size):
                    if len(orbit) != 12:
                        continue
                    counters["length12OddSubsetOrbits"] += 1
                    induced_generators = [
                        induced_permutation(orbit, block_permutation, sign_vector)
                        for block_permutation, sign_vector in generator_data
                    ]
                    lower_group = libgap.Group(induced_generators)
                    quotient_group = libgap.Group(
                        [
                            orbit_permutation(orbit, block_permutation)
                            for block_permutation, _ in generator_data
                        ]
                    )
                    if (
                        not bool(libgap.IsTransitive(lower_group, libgap.eval("[1..24]")))
                        or int(libgap.TransitiveIdentification(quotient_group)) != quotient_t
                    ):
                        continue
                    counters["sameQuotientActions"] += 1
                    lower_order = int(libgap.Size(lower_group))
                    quotient_order = int(libgap.Size(quotient_group))
                    lower_kernel_order = lower_order // quotient_order
                    if lower_order not in EXPECTED_LOWER_ORDERS[quotient_t]:
                        counters["wrongLowerOrder"] += 1
                        continue
                    if bool(full_flip in lower_group):
                        counters["flipAlreadyPresent"] += 1
                        continue
                    raised_group = libgap.Group(induced_generators + [full_flip])
                    counters["strictRankRaises"] += 1
                    raised_order = int(libgap.Size(raised_group))
                    if raised_order != 2 * lower_order:
                        raise ValueError("strict full-flip adjunction did not double the group")
                    possible_targets = {
                        target
                        for target in WANTED[quotient_t]
                        if int(libgap.Size(libgap.TransitiveGroup(24, target)))
                        == raised_order
                    }
                    if not possible_targets:
                        counters["wrongRaisedOrder"] += 1
                        continue
                    raised_t = int(libgap.TransitiveIdentification(raised_group))
                    if raised_t not in possible_targets:
                        counters["wrongRaisedLabel"] += 1
                        continue
                    survivors.append(
                        {
                            "fullFlipAlreadyPresent": False,
                            "lowerKernelOrder": lower_kernel_order,
                            "lowerKummerRank": lower_kernel_order.bit_length() - 1,
                            "lowerLabel": (
                                f"24T{int(libgap.TransitiveIdentification(lower_group))}"
                            ),
                            "lowerOrder": lower_order,
                            "quotientOrder": quotient_order,
                            "quotientT12": quotient_t,
                            "raisedKernelOrder": 2 * lower_kernel_order,
                            "raisedKummerRank": lower_kernel_order.bit_length(),
                            "raisedLabel": f"24T{raised_t}",
                            "raisedOrder": raised_order,
                            "sourceLabel": str(source["sourceLabel"]),
                            "sourceSystemIndex": system_index,
                            "sourceT": int(source["sourceT"]),
                            "subsetOrbit": [list(subset) for subset in orbit],
                            "subsetSize": subset_size,
                        }
                    )

    unique = {}
    for row in survivors:
        key = (
            row["sourceLabel"],
            row["sourceSystemIndex"],
            row["subsetSize"],
            tuple(tuple(subset) for subset in row["subsetOrbit"]),
            row["raisedLabel"],
        )
        unique[key] = row
    survivors = sorted(
        unique.values(),
        key=lambda row: (
            int(row["raisedLabel"][3:]),
            int(row["sourceLabel"][3:]),
            row["subsetSize"],
            row["subsetOrbit"],
        ),
    )
    payload = {
        "actionMap": str(ACTION_MAP.relative_to(ROOT)),
        "actionMapSha256": sha256_path(ACTION_MAP),
        "counters": dict(sorted(counters.items())),
        "mechanism": (
            "exact odd-subset signed action H; strict natural full-flip "
            "adjunction; GAP TransitiveIdentification of <H,flip>"
        ),
        "networkCalls": 0,
        "oddSubsetSizes": list(ODD_SUBSET_SIZES),
        "submissionCalls": 0,
        "survivorCount": len(survivors),
        "survivors": survivors,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "counters": payload["counters"],
                "output": str(OUTPUT.relative_to(ROOT)),
                "survivorCount": len(survivors),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
