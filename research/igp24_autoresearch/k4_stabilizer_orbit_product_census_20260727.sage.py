#!/usr/bin/env sage -python
"""Enumerate every point-stabilizer orbit-product radical from 24T18506.

The accepted source is q(x^2), so its paired roots are +/-sqrt(beta_i).
For every subset S of the beta roots invariant under the stabilizer of one
root, the twelve conjugates of prod_{j in S} beta_j give an exact rational
radicand map.  This script identifies the induced signed degree-24 action
before doing any number-field arithmetic.
"""

from itertools import combinations

from sage.all import GF, Matrix, libgap


SOURCE_T = 18506
QUOTIENT_T = 66
WANTED = {15051, 15082}


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


def image_subset(subset, permutation):
    return tuple(sorted(permutation[index] for index in subset))


def subset_orbit(subset, block_generators):
    seen = {tuple(sorted(subset))}
    queue = list(seen)
    while queue:
        current = queue.pop()
        for permutation in block_generators:
            image = image_subset(current, permutation)
            if image not in seen:
                seen.add(image)
                queue.append(image)
    return sorted(seen)


def all_subset_orbits(size, block_generators):
    remaining = set(combinations(range(12), size))
    rows = []
    while remaining:
        representative = min(remaining)
        orbit = subset_orbit(representative, block_generators)
        remaining.difference_update(orbit)
        rows.append(orbit)
    return rows


def induced_permutation(orbit, block_permutation, sign_vector):
    position = {subset: index for index, subset in enumerate(orbit)}
    images = []
    for subset in orbit:
        target_subset = image_subset(subset, block_permutation)
        target = position[target_subset]
        sign = sum(sign_vector[index] for index in subset) % 2
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def main():
    source = libgap.TransitiveGroup(24, SOURCE_T)
    generators = list(libgap.GeneratorsOfGroup(source))
    selected = None
    for block in libgap.AllBlocks(source):
        if int(libgap.Size(block)) != 2:
            continue
        orbit = libgap.Orbit(source, block, libgap.OnSets)
        if int(libgap.Size(orbit)) != 12:
            continue
        action = libgap.Action(source, orbit, libgap.OnSets)
        if int(libgap.TransitiveIdentification(action)) == QUOTIENT_T:
            selected = [
                tuple(sorted(int(value) for value in pair)) for pair in orbit
            ]
            break
    if selected is None:
        raise ValueError("missing certified block action")

    generator_data = [
        block_action_data(selected, generator) for generator in generators
    ]
    block_generators = [row[0] for row in generator_data]
    quotient = libgap.Group(
        [libgap.PermList([value + 1 for value in p]) for p in block_generators]
    )
    stabilizer = libgap.Stabilizer(quotient, 1)
    suborbits_gap = libgap.Orbits(stabilizer, libgap.eval("[1..12]"))
    suborbits = [
        tuple(sorted(int(value) - 1 for value in orbit))
        for orbit in suborbits_gap
    ]
    print(
        {
            "source": f"24T{SOURCE_T}",
            "quotient": f"12T{QUOTIENT_T}",
            "suborbits": suborbits,
            "subdegrees": [len(orbit) for orbit in suborbits],
        },
        flush=True,
    )

    rows = []
    seen_orbits = set()
    for count in range(1, len(suborbits) + 1):
        for chosen in combinations(range(len(suborbits)), count):
            subset = tuple(
                sorted(
                    value
                    for index in chosen
                    for value in suborbits[index]
                )
            )
            orbit = subset_orbit(subset, block_generators)
            if len(orbit) != 12:
                continue
            orbit_key = tuple(orbit)
            if orbit_key in seen_orbits:
                continue
            seen_orbits.add(orbit_key)
            induced_generators = [
                induced_permutation(orbit, block_permutation, sign_vector)
                for block_permutation, sign_vector in generator_data
            ]
            target = libgap.Group(induced_generators)
            if not bool(libgap.IsTransitive(target, libgap.eval("[1..24]"))):
                continue
            target_t = int(libgap.TransitiveIdentification(target))
            full_flip = libgap.PermList(
                [
                    value + 1 if value % 2 else value - 1
                    for value in range(1, 25)
                ]
            )
            twisted_target = libgap.Group(
                list(libgap.GeneratorsOfGroup(target)) + [full_flip]
            )
            twisted_t = int(libgap.TransitiveIdentification(twisted_target))
            incidence = Matrix(
                GF(2),
                12,
                12,
                lambda row, column: int(column in orbit[row]),
            )
            row = {
                "chosenSuborbits": chosen,
                "incidenceRank": int(incidence.rank()),
                "kernelOrder": int(libgap.Size(target)) // int(libgap.Size(quotient)),
                "subset": subset,
                "subsetSize": len(subset),
                "target": f"24T{target_t}",
            }
            rows.append(row)
            if target_t in WANTED:
                print({"HIT": row, "orbit": orbit}, flush=True)

    histogram = {}
    for row in rows:
        histogram[row["target"]] = histogram.get(row["target"], 0) + 1
    print({"actions": len(rows), "targetHistogram": histogram}, flush=True)
    for row in rows:
        print(row, flush=True)

    exhaustive = []
    seen_actions = set()
    for size in range(1, 12):
        for orbit in all_subset_orbits(size, block_generators):
            if len(orbit) != 12:
                continue
            induced_generators = [
                induced_permutation(orbit, block_permutation, sign_vector)
                for block_permutation, sign_vector in generator_data
            ]
            target = libgap.Group(induced_generators)
            if not bool(libgap.IsTransitive(target, libgap.eval("[1..24]"))):
                continue
            target_t = int(libgap.TransitiveIdentification(target))
            incidence = Matrix(
                GF(2),
                12,
                12,
                lambda row, column: int(column in orbit[row]),
            )
            key = (target_t, tuple(tuple(s) for s in orbit))
            if key in seen_actions:
                continue
            seen_actions.add(key)
            row = {
                "incidenceRank": int(incidence.rank()),
                "kernelOrder": int(libgap.Size(target)) // int(libgap.Size(quotient)),
                "representative": orbit[0],
                "subsetSize": size,
                "target": f"24T{target_t}",
                "twistedKernelOrder": (
                    int(libgap.Size(twisted_target)) // int(libgap.Size(quotient))
                ),
                "twistedTarget": f"24T{twisted_t}",
            }
            exhaustive.append(row)
            if target_t in WANTED or twisted_t in WANTED:
                print({"EXHAUSTIVE_HIT": row, "orbit": orbit}, flush=True)
    histogram = {}
    for row in exhaustive:
        histogram[row["target"]] = histogram.get(row["target"], 0) + 1
    twisted_histogram = {}
    for row in exhaustive:
        twisted_histogram[row["twistedTarget"]] = (
            twisted_histogram.get(row["twistedTarget"], 0) + 1
        )
    print(
        {
            "exhaustiveActions": len(exhaustive),
            "exhaustiveTargetHistogram": histogram,
            "twistedTargetHistogram": twisted_histogram,
        },
        flush=True,
    )
    for row in exhaustive:
        print({"exhaustive": row}, flush=True)


if __name__ == "__main__":
    main()
