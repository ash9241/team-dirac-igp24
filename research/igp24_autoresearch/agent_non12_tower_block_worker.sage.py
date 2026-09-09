#!/usr/bin/env sage -python
"""Enumerate exact block systems for a shard of the degree-24 library."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import libgap


POINTS = libgap.eval("[1..24]")


def point_images(permutation, degree: int) -> list[int]:
    return [int(libgap.OnPoints(point, permutation)) for point in range(1, degree + 1)]


def action_label(group, degree: int) -> str:
    if degree == 1:
        return "1T1"
    return f"{degree}T{int(libgap.TransitiveIdentification(group))}"


def census_one(t: int) -> dict:
    group = libgap.TransitiveGroup(24, t)
    order = int(libgap.Size(group))
    solvable = bool(libgap.IsSolvableGroup(group))
    row = {
        "label": f"24T{t}",
        "t": t,
        "groupOrder": order,
        "isSolvable": solvable,
        "blockSystems": [],
        "properBlockSizes": [],
        "shapes": [],
        "iteratedBlockChains": [],
    }
    if not solvable:
        return row

    systems = []
    seen_systems = set()
    for block in list(libgap.AllBlocks(group)):
        seed = tuple(sorted(int(value) for value in list(block)))
        block_size = len(seed)
        if block_size <= 1 or block_size >= 24:
            continue
        blocks = list(libgap.Orbit(group, block, libgap.OnSets))
        canonical = tuple(
            sorted(tuple(sorted(int(value) for value in list(item))) for item in blocks)
        )
        payload = json.dumps(canonical, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
        if digest in seen_systems:
            continue
        seen_systems.add(digest)
        block_count = len(blocks)
        action = libgap.ActionHomomorphism(group, libgap.AsSet(blocks), libgap.OnSets)
        quotient = libgap.Image(action)
        stabilizer = libgap.Stabilizer(group, block, libgap.OnSets)
        fiber = libgap.Action(stabilizer, block, libgap.OnPoints)
        system = {
            "blockCount": block_count,
            "blockKernelOrder": int(libgap.Size(libgap.Kernel(action))),
            "blockSize": block_size,
            "fiberActionLabel": action_label(fiber, block_size),
            "fiberActionOrder": int(libgap.Size(fiber)),
            "quotientActionLabel": action_label(quotient, block_count),
            "quotientActionOrder": int(libgap.Size(quotient)),
            "seedBlock": list(seed),
            "shape": f"{block_count}x{block_size}",
            "systemSha256": digest,
        }
        systems.append(system)

    systems.sort(
        key=lambda item: (
            int(item["blockSize"]),
            str(item["quotientActionLabel"]),
            str(item["fiberActionLabel"]),
            str(item["systemSha256"]),
        )
    )
    chains = set()
    for left in systems:
        left_seed = set(left["seedBlock"])
        for right in systems:
            right_seed = set(right["seedBlock"])
            if left_seed < right_seed:
                chains.add(
                    (
                        int(left["blockSize"]),
                        int(right["blockSize"]),
                        str(left["systemSha256"]),
                        str(right["systemSha256"]),
                    )
                )
    row["blockSystems"] = systems
    row["properBlockSizes"] = sorted({int(item["blockSize"]) for item in systems})
    row["shapes"] = sorted({str(item["shape"]) for item in systems})
    row["iteratedBlockChains"] = [
        {
            "innerBlockSize": inner,
            "outerBlockSize": outer,
            "innerSystemSha256": inner_hash,
            "outerSystemSha256": outer_hash,
        }
        for inner, outer, inner_hash, outer_hash in sorted(chains)
    ]
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input is not None:
        if args.start is not None or args.end is not None:
            parser.error("--input is mutually exclusive with --start/--end")
        values = [int(line) for line in args.input.read_text().splitlines() if line.strip()]
    else:
        if args.start is None or args.end is None:
            parser.error("provide either --input or both --start and --end")
        if args.start < 1 or args.end < args.start or args.end > 25000:
            parser.error("require 1 <= start <= end <= 25000")
        values = list(range(args.start, args.end + 1))
    if not values or len(values) != len(set(values)) or any(t < 1 or t > 25000 for t in values):
        parser.error("input labels must be distinct integers in [1,25000]")
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for t in values:
            handle.write(json.dumps(census_one(t), separators=(",", ":"), sort_keys=True))
            handle.write("\n")
    temporary.replace(args.output)
    print(json.dumps({"start": min(values), "end": max(values), "rows": len(values), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
