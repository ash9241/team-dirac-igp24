#!/usr/bin/env sage -python
"""Return every permutation cycle partition occurring in one 24T action."""

from __future__ import annotations

import argparse
import json

from sage.all import libgap


def cycle_types(label: str, t: int) -> dict:
    group = libgap.TransitiveGroup(24, t)
    points = libgap.eval("[1..24]")
    partitions = {
        tuple(sorted(int(value) for value in libgap.CycleLengths(
            libgap.Representative(conjugacy_class), points
        )))
        for conjugacy_class in libgap.ConjugacyClasses(group)
    }
    return {
        "label": label,
        "t": t,
        "cycleTypes": [list(partition) for partition in sorted(partitions)],
        "cycleTypeCount": len(partitions),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("t", type=int)
    args = parser.parse_args()
    print(json.dumps(cycle_types(args.label, args.t), separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
