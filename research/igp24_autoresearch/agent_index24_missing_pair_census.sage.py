#!/usr/bin/env sage -python
"""Exact unordered-pair census for currently owned labels absent from prior maps."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
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


def census_one(source: dict, pairs, gold_by_label: dict[str, set[int]]) -> dict:
    group = libgap.TransitiveGroup(24, int(source["t"]))
    orbits = list(libgap.Orbits(group, pairs, libgap.OnSets))
    orbit_sizes = [int(libgap.Length(orbit)) for orbit in orbits]
    targets = []
    for orbit_index, orbit in enumerate(orbits):
        if int(libgap.Length(orbit)) != 24:
            continue
        action = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
        image = libgap.Image(action)
        target_t = int(libgap.TransitiveIdentification(image))
        targets.append(
            {
                "action": action,
                "imageOrder": int(libgap.Size(image)),
                "kernelOrder": int(libgap.Size(libgap.Kernel(action))),
                "orbitIndex": orbit_index,
                "orbitSize": 24,
                "targetLabel": f"24T{target_t}",
                "targetT": target_t,
            }
        )

    profiles = []
    relevant_r = {int(value) for value in source["sourceR"]}
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(group)):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        source_r = fixed_points(representative)
        if source_r not in relevant_r:
            continue
        profiles.append(
            {
                "classIndex": class_index,
                "classSize": int(libgap.Size(conjugacy_class)),
                "order": order,
                "sourceR": source_r,
                "targets": [
                    {
                        "orbitIndex": int(target["orbitIndex"]),
                        "targetLabel": str(target["targetLabel"]),
                        "targetR": fixed_points(libgap.Image(target["action"], representative)),
                    }
                    for target in targets
                ],
            }
        )

    routes = []
    for target in targets:
        target_label = str(target["targetLabel"])
        if target_label == str(source["label"]):
            continue
        gold_r = gold_by_label.get(target_label, set())
        if not gold_r:
            continue
        for source_r in sorted(relevant_r):
            mapped = sorted(
                {
                    int(signature["targetR"])
                    for profile in profiles
                    if int(profile["sourceR"]) == source_r
                    for signature in profile["targets"]
                    if int(signature["orbitIndex"]) == int(target["orbitIndex"])
                }
            )
            if not set(mapped).intersection(gold_r):
                continue
            routes.append(
                {
                    "allCompatibleClassesGold": bool(mapped) and set(mapped) <= gold_r,
                    "deterministicTargetR": mapped[0] if len(mapped) == 1 else None,
                    "goldR": sorted(gold_r),
                    "mappedTargetR": mapped,
                    "orbitIndex": int(target["orbitIndex"]),
                    "sourceR": source_r,
                    "targetLabel": target_label,
                    "targetT": int(target["targetT"]),
                }
            )

    serial_targets = [
        {key: value for key, value in target.items() if key != "action"}
        for target in targets
    ]
    result = {
        "actionKind": "unordered_pair_missing_from_prior_maps",
        "length24OrbitCount": len(targets),
        "orbitSizes": orbit_sizes,
        "profiles": profiles,
        "routes": routes,
        "sourceLabel": str(source["label"]),
        "sourceOrder": int(libgap.Size(group)),
        "sourceR": sorted(relevant_r),
        "sourceT": int(source["t"]),
        "status": "certified",
        "targetCounts": dict(sorted(Counter(row["targetLabel"] for row in targets).items())),
        "targets": serial_targets,
    }
    result["exactCertificateSha256"] = hashlib.sha256(
        json.dumps(result, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--prior-map", type=Path, action="append", required=True,
        help="prior action map; repeat to use several immutable shards",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=6)
    parser.add_argument("--checkpoint-every", type=int, default=5)
    parser.add_argument(
        "--signature-aware",
        action="store_true",
        help=(
            "recompute a source label when the frozen input adds a real-root "
            "signature absent from the prior frozen group input"
        ),
    )
    parser.add_argument(
        "--prior-input",
        type=Path,
        action="append",
        default=[],
        help=(
            "prior frozen group input used to identify added source signatures; "
            "repeatable and required with --signature-aware"
        ),
    )
    args = parser.parse_args()

    input_rows = read_jsonl(args.input)
    gold_by_label = {
        str(row["label"]): {int(value) for value in row["goldR"]}
        for row in input_rows
        if row["goldR"]
    }
    prior_rows = [row for path in args.prior_map for row in read_jsonl(path)]
    if args.signature_aware:
        if not args.prior_input:
            parser.error("--signature-aware requires at least one --prior-input")
        mapped = {str(row["sourceLabel"]) for row in prior_rows}
        prior_signatures: dict[str, set[int]] = {}
        for path in args.prior_input:
            for row in read_jsonl(path):
                if not row.get("isOwnedSource"):
                    continue
                label = str(row["label"])
                prior_signatures.setdefault(label, set()).update(
                    int(value) for value in row.get("sourceR", [])
                )
        missing = []
        for row in input_rows:
            if not row["isOwnedSource"]:
                continue
            label = str(row["label"])
            current = {int(value) for value in row["sourceR"]}
            added = current - prior_signatures.get(label, set())
            if label in mapped and not added:
                continue
            selected_row = dict(row)
            selected_row["sourceR"] = sorted(added or current)
            missing.append(selected_row)
    else:
        mapped = {str(row["sourceLabel"]) for row in prior_rows}
        missing = [
            row
            for row in input_rows
            if row["isOwnedSource"] and str(row["label"]) not in mapped
        ]
    selected = [
        row for index, row in enumerate(missing)
        if index % args.shard_count == args.shard_index
    ]
    pairs = libgap.Combinations(libgap.eval("[1..24]"), 2)
    rows = []
    for index, source in enumerate(selected, start=1):
        try:
            row = census_one(source, pairs, gold_by_label)
        except Exception as exc:
            row = {
                "actionKind": "unordered_pair_missing_from_prior_maps",
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
    routes = sum(len(row.get("routes", [])) for row in rows)
    safe = sum(
        bool(route["allCompatibleClassesGold"])
        for row in rows for route in row.get("routes", [])
    )
    print(json.dumps({"errors": errors, "rows": len(rows), "routes": routes, "safe": safe, "shard": args.shard_index}))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
