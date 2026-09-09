#!/usr/bin/env sage -python
"""Compute exact cycle/signature profiles for missing live D4 target groups."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sage.all import libgap


POINTS = libgap.eval("[1..24]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--structures", type=Path)
    parser.add_argument("--known-profiles", type=Path)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (0 <= args.shard_index < args.shard_count):
        parser.error("require 0 <= shard-index < shard-count")

    if args.structures:
        structural = set()
        with args.structures.open(encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if any(
                    system.get("shape") == "6x4"
                    and system.get("fiberActionLabel") == "4T3"
                    and system.get("quotientActionLabel") == "6T11"
                    for system in row.get("blockSystems", [])
                ):
                    structural.add(str(row["label"]))
        known = set()
        if args.known_profiles:
            known_payload = json.loads(args.known_profiles.read_text(encoding="utf-8"))
            known = {str(row["label"]) for row in known_payload["groups"]}
        labels = sorted(structural - known, key=lambda value: int(value[3:]))
    elif args.catalog:
        payload = json.loads(args.catalog.read_text(encoding="utf-8"))
        labels = sorted(
            payload["missingProfileLabels"], key=lambda value: int(value[3:])
        )
    else:
        parser.error("provide --catalog or --structures")
    labels = labels[args.shard_index :: args.shard_count]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for offset, label in enumerate(labels, start=1):
            t = int(label[3:])
            group = libgap.TransitiveGroup(24, t)
            classes = list(libgap.ConjugacyClasses(group))
            representatives = [libgap.Representative(item) for item in classes]
            profiles = sorted(
                {
                    tuple(
                        sorted(
                            int(value)
                            for value in libgap.CycleLengths(representative, POINTS)
                        )
                    )
                    for representative in representatives
                }
            )
            possible_real_roots = sorted(
                {
                    24 - int(libgap.NrMovedPoints(representative))
                    for representative in representatives
                    if int(libgap.Order(representative)) in (1, 2)
                }
            )
            row = {
                "classCount": len(classes),
                "cycleProfiles": [list(profile) for profile in profiles],
                "label": label,
                "order": int(libgap.Size(group)),
                "possibleRealRoots": possible_real_roots,
                "t": t,
            }
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            print(
                json.dumps(
                    {
                        "label": label,
                        "progress": [offset, len(labels)],
                        "profiles": len(profiles),
                        "shard": [args.shard_index, args.shard_count],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
