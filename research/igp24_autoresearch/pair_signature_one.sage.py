#!/usr/bin/env sage -python
"""Compute exact complex-conjugation signatures on length-24 pair orbits."""

from __future__ import annotations

import argparse
import json

from sage.all import libgap


def fixed_points(permutation, degree: int) -> int:
    return degree - int(libgap.NrMovedPoints(permutation))


def signature_profiles(label: str, t: int) -> dict:
    group = libgap.TransitiveGroup(24, t)
    pairs = libgap.Combinations(libgap.eval("[1..24]"), 2)
    pair_orbits = list(libgap.Orbits(group, pairs, libgap.OnSets))
    length_24 = []
    for orbit_index, orbit in enumerate(pair_orbits):
        if int(libgap.Length(orbit)) != 24:
            continue
        homomorphism = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
        image = libgap.Image(homomorphism)
        length_24.append(
            {
                "orbitIndex": orbit_index,
                "targetLabel": f"24T{int(libgap.TransitiveIdentification(image))}",
                "homomorphism": homomorphism,
            }
        )

    profiles = []
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(group)):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        source_r = fixed_points(representative, 24)
        orbit_signatures = []
        for orbit in length_24:
            image_element = libgap.Image(orbit["homomorphism"], representative)
            orbit_signatures.append(
                {
                    "orbitIndex": orbit["orbitIndex"],
                    "targetLabel": orbit["targetLabel"],
                    "targetR": fixed_points(image_element, 24),
                }
            )
        profiles.append(
            {
                "classIndex": class_index,
                "classSize": int(libgap.Size(conjugacy_class)),
                "order": order,
                "sourceR": source_r,
                "orbitSignatures": orbit_signatures,
            }
        )
    return {
        "sourceLabel": label,
        "sourceT": t,
        "length24OrbitCount": len(length_24),
        "profiles": profiles,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("t", type=int)
    args = parser.parse_args()
    print(
        json.dumps(
            signature_profiles(args.label, args.t),
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
