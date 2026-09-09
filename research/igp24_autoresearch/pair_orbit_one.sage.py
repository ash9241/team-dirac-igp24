#!/usr/bin/env sage -python
"""Crash-isolated exact unordered-pair action map for one 24T group."""

from __future__ import annotations

import argparse
import json
from collections import Counter

from sage.all import libgap


def orbit_map(label: str, t: int) -> dict:
    group = libgap.TransitiveGroup(24, t)
    pairs = libgap.Combinations(libgap.eval("[1..24]"), 2)
    orbits = list(libgap.Orbits(group, pairs, libgap.OnSets))
    orbit_sizes = [int(libgap.Length(orbit)) for orbit in orbits]
    targets = []
    for orbit_index, orbit in enumerate(orbits):
        if int(libgap.Length(orbit)) != 24:
            continue
        homomorphism = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
        image = libgap.Image(homomorphism)
        target_t = int(libgap.TransitiveIdentification(image))
        targets.append(
            {
                "orbitIndex": orbit_index,
                "orbitSize": 24,
                "targetLabel": f"24T{target_t}",
                "targetT": target_t,
                "imageOrder": int(libgap.Size(image)),
                "kernelOrder": int(libgap.Size(libgap.Kernel(homomorphism))),
            }
        )
    counts = Counter(row["targetLabel"] for row in targets)
    return {
        "sourceLabel": label,
        "sourceT": t,
        "sourceOrder": int(libgap.Size(group)),
        "orbitSizes": orbit_sizes,
        "length24OrbitCount": len(targets),
        "targetCounts": dict(sorted(counts.items())),
        "targets": targets,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("t", type=int)
    args = parser.parse_args()
    print(json.dumps(orbit_map(args.label, args.t), separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
