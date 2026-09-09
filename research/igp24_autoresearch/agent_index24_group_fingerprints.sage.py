#!/usr/bin/env sage -python
"""Exact isomorphism-invariant fingerprints for the frozen 24T universe.

The fingerprint is used only as a rejection filter.  A surviving pair is still
proved isomorphic (or non-isomorphic) with GAP's IsomorphismGroups.  For the
very largest orders, where conjugacy-class enumeration can dominate, the
fingerprint is intentionally left unavailable and no pair is rejected by it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import libgap


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def abelian_invariants(group):
    return sorted(int(value) for value in libgap.AbelianInvariants(group))


def fingerprint_one(row: dict, max_profile_order: int) -> dict:
    group = libgap.TransitiveGroup(24, int(row["t"]))
    order = int(libgap.Size(group))
    result = {
        **row,
        "abelianInvariants": abelian_invariants(group),
        "centerAbelianInvariants": abelian_invariants(libgap.Center(group)),
        "centerOrder": int(libgap.Size(libgap.Center(group))),
        "derivedAbelianInvariants": abelian_invariants(libgap.DerivedSubgroup(group)),
        "derivedOrder": int(libgap.Size(libgap.DerivedSubgroup(group))),
        "isAbelian": bool(libgap.IsAbelian(group)),
        "isNilpotent": bool(libgap.IsNilpotentGroup(group)),
        "isPerfect": bool(libgap.IsPerfectGroup(group)),
        "isSolvable": bool(libgap.IsSolvableGroup(group)),
        "order": str(order),
        "status": "certified",
    }
    derived = libgap.DerivedSubgroup(group)
    result["derivedCenterOrder"] = int(libgap.Size(libgap.Center(derived)))
    result["secondDerivedOrder"] = int(libgap.Size(libgap.DerivedSubgroup(derived)))

    if order <= max_profile_order:
        profile = sorted(
            (
                int(libgap.Order(libgap.Representative(conjugacy_class))),
                int(libgap.Size(conjugacy_class)),
            )
            for conjugacy_class in libgap.ConjugacyClasses(group)
        )
        payload = json.dumps(profile, separators=(",", ":"))
        result["conjugacyClassCount"] = len(profile)
        result["conjugacyProfileSha256"] = hashlib.sha256(payload.encode()).hexdigest()
    else:
        result["conjugacyClassCount"] = None
        result["conjugacyProfileSha256"] = None

    invariant_payload = {
        key: result[key]
        for key in (
            "abelianInvariants",
            "centerAbelianInvariants",
            "centerOrder",
            "conjugacyClassCount",
            "conjugacyProfileSha256",
            "derivedAbelianInvariants",
            "derivedCenterOrder",
            "derivedOrder",
            "isAbelian",
            "isNilpotent",
            "isPerfect",
            "isSolvable",
            "order",
            "secondDerivedOrder",
        )
    }
    result["fingerprintSha256"] = hashlib.sha256(
        json.dumps(invariant_payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=6)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--max-profile-order", type=int, default=100_000_000)
    args = parser.parse_args()

    selected = [
        row
        for index, row in enumerate(read_jsonl(args.input))
        if index % args.shard_count == args.shard_index
    ]
    output = []
    for index, row in enumerate(selected, start=1):
        try:
            value = fingerprint_one(row, args.max_profile_order)
        except Exception as exc:
            value = {
                **row,
                "error": f"{type(exc).__name__}: {exc}",
                "status": "error",
            }
        output.append(value)
        if args.checkpoint_every and index % args.checkpoint_every == 0:
            write_jsonl(args.output, output)
            print(
                json.dumps(
                    {"completed": index, "shard": args.shard_index, "total": len(selected)}
                ),
                flush=True,
            )
    write_jsonl(args.output, output)
    errors = sum(row["status"] != "certified" for row in output)
    print(json.dumps({"errors": errors, "rows": len(output), "shard": args.shard_index}))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
