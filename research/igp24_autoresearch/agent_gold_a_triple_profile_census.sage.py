#!/usr/bin/env sage -python
"""Exact real-signature profiles for certified degree-24 triple actions.

The input rows come only from the four durable 3-subset orbit certificates.
For every selected source group this worker recomputes the action on all
unordered triples, verifies the complete orbit census, and records the image
of every identity/involution class on every length-24 orbit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import libgap


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True))
            handle.write("\n")
    temporary.replace(path)


def fixed_points(permutation, degree: int = 24) -> int:
    return degree - int(libgap.NrMovedPoints(permutation))


def canonical_sha256(value) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def profile_one(certificate: dict, triples) -> dict:
    source_t = int(certificate["sourceT"])
    source_label = str(certificate["sourceLabel"])
    group = libgap.TransitiveGroup(24, source_t)
    if int(libgap.Size(group)) != int(certificate["sourceOrder"]):
        raise ArithmeticError("source group order does not match triple certificate")

    orbits = list(libgap.Orbits(group, triples, libgap.OnSets))
    orbit_sizes = [int(libgap.Length(orbit)) for orbit in orbits]
    if orbit_sizes != [int(value) for value in certificate["orbitSizes"]]:
        raise ArithmeticError("full triple-orbit size census changed")

    length24 = []
    for orbit_index, orbit in enumerate(orbits):
        if int(libgap.Length(orbit)) != 24:
            continue
        homomorphism = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
        image = libgap.Image(homomorphism)
        target_t = int(libgap.TransitiveIdentification(image))
        length24.append(
            {
                "homomorphism": homomorphism,
                "imageOrder": int(libgap.Size(image)),
                "kernelOrder": int(libgap.Size(libgap.Kernel(homomorphism))),
                "orbitIndex": orbit_index,
                "targetLabel": f"24T{target_t}",
                "targetT": target_t,
            }
        )
    actual_targets = [
        {key: value for key, value in row.items() if key != "homomorphism"}
        for row in length24
    ]
    expected_targets = [
        {
            key: row[key]
            for key in (
                "imageOrder",
                "kernelOrder",
                "orbitIndex",
                "targetLabel",
                "targetT",
            )
        }
        for row in certificate["targets"]
    ]
    if actual_targets != expected_targets:
        raise ArithmeticError("degree-24 triple targets changed from certificate")

    owned_source_rs = {int(value) for value in certificate["sourceR"]}
    profiles = []
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(group)):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        source_r = fixed_points(representative)
        if source_r not in owned_source_rs:
            continue
        signatures = []
        for orbit in length24:
            image_element = libgap.Image(orbit["homomorphism"], representative)
            signatures.append(
                {
                    "orbitIndex": int(orbit["orbitIndex"]),
                    "targetLabel": str(orbit["targetLabel"]),
                    "targetR": fixed_points(image_element),
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
    output = {
        "actionKind": "unordered_3_subset",
        "certificateRowSha256": str(certificate["certificateRowSha256"]),
        "length24OrbitCount": len(length24),
        "orbitSizes": orbit_sizes,
        "profiles": profiles,
        "sourceLabel": source_label,
        "sourceOrder": int(certificate["sourceOrder"]),
        "sourceR": sorted(owned_source_rs),
        "sourceT": source_t,
        "status": "certified",
        "targets": actual_targets,
    }
    output["exactProfileSha256"] = canonical_sha256(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=6)
    parser.add_argument("--checkpoint-every", type=int, default=5)
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shard_count:
        parser.error("invalid shard index")

    candidates = read_jsonl(args.input)
    selected = [
        row
        for index, row in enumerate(candidates)
        if index % args.shard_count == args.shard_index
    ]
    triples = libgap.Combinations(libgap.eval("[1..24]"), 3)
    rows = []
    for index, certificate in enumerate(selected, start=1):
        try:
            row = profile_one(certificate, triples)
        except Exception as exc:
            row = {
                "actionKind": "unordered_3_subset",
                "certificateRowSha256": certificate.get("certificateRowSha256"),
                "error": f"{type(exc).__name__}: {exc}",
                "sourceLabel": certificate["sourceLabel"],
                "sourceT": certificate["sourceT"],
                "status": "error",
            }
        rows.append(row)
        if args.checkpoint_every and index % args.checkpoint_every == 0:
            write_jsonl(args.output, rows)
            print(
                json.dumps(
                    {
                        "completed": index,
                        "shard": args.shard_index,
                        "total": len(selected),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    write_jsonl(args.output, rows)
    summary = {
        "certified": sum(row["status"] == "certified" for row in rows),
        "errors": sum(row["status"] != "certified" for row in rows),
        "output": str(args.output.resolve()),
        "rows": len(rows),
        "shard": args.shard_index,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if not summary["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
