#!/usr/bin/env sage -python
"""Map full C3^8 fiber kernels over all degree-8 transitive actions."""

from __future__ import annotations

import json

from sage.all import GF, libgap


def perm_from_images(images):
    return libgap.PermList(libgap([int(value) for value in images]))


def lifted_group(source, inversion, cocycle):
    generators = []
    for block in range(8):
        images = list(range(1, 25))
        base = 3 * block
        images[base : base + 3] = [base + 2, base + 3, base + 1]
        generators.append(perm_from_images(images))
    for generator in libgap.GeneratorsOfGroup(source):
        images = []
        for block in range(1, 9):
            image_block = int(libgap.OnPoints(block, generator))
            for fiber in range(3):
                if cocycle == "source_sign" and int(libgap.SignPerm(generator)) == -1:
                    image_fiber = (-fiber) % 3
                else:
                    image_fiber = fiber
                images.append(3 * (image_block - 1) + image_fiber + 1)
        generators.append(perm_from_images(images))
    if inversion:
        images = []
        for block in range(8):
            images.extend([3 * block + 1, 3 * block + 3, 3 * block + 2])
        generators.append(perm_from_images(images))
    return libgap.Group(generators)


rows = []
for inversion in (False, True):
  for cocycle in ("trivial", "source_sign"):
    for source_t in range(1, 51):
        source = libgap.TransitiveGroup(8, source_t)
        target = lifted_group(source, inversion, cocycle)
        rows.append(
            {
                "inversion": inversion,
                "cocycle": cocycle,
                "sourceLabel": f"8T{source_t}",
                "sourceOrder": int(libgap.Size(source)),
                "targetLabel": f"24T{int(libgap.TransitiveIdentification(target))}",
                "targetOrder": int(libgap.Size(target)),
            }
        )
print(json.dumps(rows, sort_keys=True))
