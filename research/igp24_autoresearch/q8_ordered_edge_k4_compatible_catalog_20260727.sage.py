#!/usr/bin/env sage
"""Exhaustive structural catalog for the ordered-edge 12T8 Kummer lane.

Every degree-24 transitive group is inspected, not merely groups occurring in
the current target snapshot.  A group is retained when it has a 12-by-2 block
system whose quotient action is 12T8 and whose block kernel is a 2-group of
order at most 2^9.  This is the deliberately broad envelope forced by the
ordered-edge quotient and the four K4 star square relations.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "q8_ordered_edge_k4_compatible_catalog_20260727.json"
POINTS = libgap.eval("[1..24]")
ALLOWED_ORDERS = {24 * 2**rank for rank in range(10)}


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def canonical_system(group, block) -> tuple[tuple[int, int], ...]:
    return tuple(
        sorted(
            tuple(sorted(int(value) for value in item))
            for item in libgap.Orbit(group, block, libgap.OnSets)
        )
    )


def cycle_type(permutation) -> tuple[int, ...]:
    return tuple(
        sorted(
            int(value)
            for value in libgap.CycleLengths(permutation, POINTS)
        )
    )


def is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def inspect_group(t: int) -> dict | None:
    group = libgap.TransitiveGroup(24, t)
    order = int(libgap.Size(group))
    if order not in ALLOWED_ORDERS:
        return None

    systems = []
    seen = set()
    for block in libgap.AllBlocks(group):
        if int(libgap.Size(block)) != 2:
            continue
        system = canonical_system(group, block)
        if len(system) != 12 or system in seen:
            continue
        seen.add(system)
        gap_system = libgap.AsSet(
            [libgap.Set(list(pair)) for pair in system]
        )
        homomorphism = libgap.ActionHomomorphism(
            group, gap_system, libgap.OnSets
        )
        quotient = libgap.Image(homomorphism)
        if (
            int(libgap.Size(quotient)) != 24
            or int(libgap.TransitiveIdentification(quotient)) != 8
        ):
            continue
        kernel_order = int(libgap.Size(libgap.Kernel(homomorphism)))
        if kernel_order > 2**9 or not is_power_of_two(kernel_order):
            continue
        systems.append(
            {
                "kernelOrder": kernel_order,
                "kernelRank": kernel_order.bit_length() - 1,
                "seedBlock": list(system[0]),
                "system": [list(pair) for pair in system],
            }
        )

    if not systems:
        return None
    profiles = sorted(
        {
            cycle_type(libgap.Representative(conjugacy_class))
            for conjugacy_class in libgap.ConjugacyClasses(group)
        }
    )
    possible_real_roots = sorted(
        {
            24 - int(
                libgap.NrMovedPoints(
                    libgap.Representative(conjugacy_class)
                )
            )
            for conjugacy_class in libgap.ConjugacyClasses(group)
            if int(
                libgap.Order(libgap.Representative(conjugacy_class))
            )
            in (1, 2)
        }
    )
    return {
        "label": f"24T{t}",
        "t": t,
        "order": order,
        "systems": systems,
        "cycleProfiles": [list(profile) for profile in profiles],
        "possibleRealRoots": possible_real_roots,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=25000)
    args = parser.parse_args()
    if not (1 <= args.start <= args.end <= 25000):
        parser.error("require 1 <= start <= end <= 25000")

    rows = []
    order_candidates = 0
    for t in range(args.start, args.end + 1):
        group = libgap.TransitiveGroup(24, t)
        if int(libgap.Size(group)) in ALLOWED_ORDERS:
            order_candidates += 1
            row = inspect_group(t)
            if row is not None:
                rows.append(row)
        if t % 1000 == 0:
            print(
                json.dumps(
                    {
                        "checked": t,
                        "orderCandidates": order_candidates,
                        "compatible": len(rows),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    payload = {
        "schemaVersion": "q8-ordered-edge-compatible-catalog-v1",
        "range": [args.start, args.end],
        "exhaustiveDegree24": args.start == 1 and args.end == 25000,
        "quotientLabel": "12T8",
        "maximumKernelOrder": 2**9,
        "allowedGroupOrders": sorted(ALLOWED_ORDERS),
        "orderCandidates": order_candidates,
        "compatibleCount": len(rows),
        "includesSplit24T10377": any(row["t"] == 10377 for row in rows),
        "groups": rows,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(OUTPUT, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "compatibleCount": len(rows),
                "includesSplit24T10377": payload["includesSplit24T10377"],
                "labels": [row["label"] for row in rows],
                "output": str(OUTPUT),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
