#!/usr/bin/env sage -python
"""Identify every eight-block quotient action of a degree-24 target."""

from __future__ import annotations

import argparse
import json

from sage.all import libgap


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-t", type=int, required=True)
    args = parser.parse_args()
    group = libgap.TransitiveGroup(24, args.target_t)
    rows = []
    seen = set()
    for block in libgap.AllBlocks(group):
        if int(libgap.Length(block)) != 3:
            continue
        blocks = libgap.Orbit(group, block, libgap.OnSets)
        action = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
        image = libgap.Image(action)
        kernel = libgap.Kernel(action)
        key = (int(libgap.TransitiveIdentification(image)), int(libgap.Size(kernel)))
        if key not in seen:
            seen.add(key)
            rows.append(
                {
                    "blockActionLabel": f"8T{key[0]}",
                    "blockCount": int(libgap.Length(blocks)),
                    "blockSize": 3,
                    "kernelOrder": key[1],
                    "quotientOrder": int(libgap.Size(image)),
                }
            )
    print(json.dumps({"targetT": args.target_t, "targetOrder": int(libgap.Size(group)), "systems": rows}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
