#!/usr/bin/env sage -python
"""Exact degree-24 orbit census for the ordered-pair action."""

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


def fixed_points(permutation) -> int:
    return 24 - int(libgap.NrMovedPoints(permutation))


def census_one(source, ordered_pairs, gold_by_label, exclusions):
    group = libgap.TransitiveGroup(24, int(source["t"]))
    orbits = list(libgap.Orbits(group, ordered_pairs, libgap.OnTuples))
    orbit_sizes = [int(libgap.Length(orbit)) for orbit in orbits]
    length24 = []
    for orbit_index, orbit in enumerate(orbits):
        if int(libgap.Length(orbit)) != 24:
            continue
        action = libgap.ActionHomomorphism(group, orbit, libgap.OnTuples)
        image = libgap.Image(action)
        target_t = int(libgap.TransitiveIdentification(image))
        length24.append(
            {
                "action": action,
                "imageOrder": int(libgap.Size(image)),
                "kernelOrder": int(libgap.Size(libgap.Kernel(action))),
                "orbitIndex": orbit_index,
                "targetLabel": f"24T{target_t}",
                "targetT": target_t,
            }
        )
    profiles = []
    source_rs = {int(value) for value in source["sourceR"]}
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(group)):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        source_r = fixed_points(representative)
        if source_r not in source_rs:
            continue
        signatures = []
        for orbit in length24:
            signatures.append(
                {
                    "orbitIndex": int(orbit["orbitIndex"]),
                    "targetLabel": str(orbit["targetLabel"]),
                    "targetR": fixed_points(libgap.Image(orbit["action"], representative)),
                }
            )
        profiles.append(
            {
                "classIndex": class_index,
                "classSize": int(libgap.Size(conjugacy_class)),
                "order": order,
                "sourceR": source_r,
                "orbitSignatures": signatures,
            }
        )
    serial_targets = [
        {key: value for key, value in row.items() if key != "action"}
        for row in length24
    ]
    support = []
    excluded = exclusions.get(str(source["label"]), set())
    for profile in profiles:
        for signature in profile["orbitSignatures"]:
            label = str(signature["targetLabel"])
            target_r = int(signature["targetR"])
            if label in excluded or target_r not in gold_by_label.get(label, set()):
                continue
            support.append(
                {
                    "classIndex": int(profile["classIndex"]),
                    "classSize": int(profile["classSize"]),
                    "orbitIndex": int(signature["orbitIndex"]),
                    "sourceR": int(profile["sourceR"]),
                    "targetLabel": label,
                    "targetR": target_r,
                }
            )
    output = {
        "actionKind": "ordered_pair",
        "length24OrbitCount": len(length24),
        "orbitSizes": orbit_sizes,
        "profiles": profiles,
        "sourceLabel": str(source["label"]),
        "sourceR": sorted(source_rs),
        "sourceT": int(source["t"]),
        "status": "certified",
        "structuralGoldSupport": support,
        "targets": serial_targets,
    }
    output["exactCertificateSha256"] = hashlib.sha256(
        json.dumps(output, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--exclusions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=6)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    args = parser.parse_args()
    input_rows = read_jsonl(args.input)
    gold_by_label = {
        str(row["label"]): {int(value) for value in row["goldR"]}
        for row in input_rows
        if row["goldR"]
    }
    exclusions = {
        str(row["sourceLabel"]): set(row["excludedTargetLabels"])
        for row in read_jsonl(args.exclusions)
    }
    sources = [row for row in input_rows if row["isOwnedSource"]]
    selected = [
        row for index, row in enumerate(sources)
        if index % args.shard_count == args.shard_index
    ]
    ordered_pairs = libgap.eval("Filtered(Cartesian([1..24],[1..24]),x->x[1]<>x[2])")
    rows = []
    for index, source in enumerate(selected, start=1):
        try:
            row = census_one(source, ordered_pairs, gold_by_label, exclusions)
        except Exception as exc:
            row = {
                "actionKind": "ordered_pair",
                "error": f"{type(exc).__name__}: {exc}",
                "sourceLabel": source["label"],
                "sourceT": source["t"],
                "status": "error",
            }
        rows.append(row)
        if args.checkpoint_every and index % args.checkpoint_every == 0:
            write_jsonl(args.output, rows)
            print(json.dumps({"completed": index, "shard": args.shard_index, "total": len(selected)}), flush=True)
    write_jsonl(args.output, rows)
    errors = sum(row["status"] != "certified" for row in rows)
    support = sum(len(row.get("structuralGoldSupport", [])) for row in rows)
    print(json.dumps({"errors": errors, "rows": len(rows), "shard": args.shard_index, "support": support}))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
