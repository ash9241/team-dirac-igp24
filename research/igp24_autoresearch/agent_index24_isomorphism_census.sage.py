#!/usr/bin/env sage -python
"""Exact core-free index-24 subgroup classes for candidate 24T isomorphisms."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
FINGERPRINT_FIELDS = (
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
_FINGERPRINTS_BY_T = None


# GAP's default radical isomorphism path (``PatheticIsomorphism``) can raise
# internal collector errors for some solvable degree-24 groups and poison all
# later calls in the same process.  The documented ``forcetest := "old"``
# option selects the stable Morphium path.  The surrounding fingerprints are
# only rejection filters; this call remains the exact isomorphism proof.
ISOMORPHISM_GROUPS_OLD = libgap.eval(
    'function(G,H) return IsomorphismGroups(G,H:forcetest:="old"); end'
)


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def fingerprints_by_t():
    global _FINGERPRINTS_BY_T
    if _FINGERPRINTS_BY_T is None:
        rows = []
        for path in sorted((ROOT / "data").glob("agent_index24_group_fingerprint_shard*.jsonl")):
            rows.extend(read_jsonl(path))
        _FINGERPRINTS_BY_T = {int(row["t"]): row for row in rows}
    return _FINGERPRINTS_BY_T


def exact_fingerprint_rejection(candidate: dict) -> dict | None:
    fingerprints = fingerprints_by_t()
    source = fingerprints.get(int(candidate["sourceT"]))
    target = fingerprints.get(int(candidate["targetT"]))
    if source is None or target is None:
        return None
    if source.get("status") != "certified" or target.get("status") != "certified":
        return None
    if source["fingerprintSha256"] == target["fingerprintSha256"]:
        return None
    differences = {
        field: {"source": source.get(field), "target": target.get(field)}
        for field in FINGERPRINT_FIELDS
        if source.get(field) != target.get(field)
    }
    if not differences:
        raise ArithmeticError("fingerprint hashes differ without an invariant difference")
    return {
        "differences": differences,
        "sourceFingerprintSha256": str(source["fingerprintSha256"]),
        "targetFingerprintSha256": str(target["fingerprintSha256"]),
        "type": "exact_isomorphism_invariant_mismatch",
    }


def fixed_points(permutation) -> int:
    return 24 - int(libgap.NrMovedPoints(permutation))


def subgroup_identity(source_label: str, target_label: str, subgroup) -> str:
    generators = sorted(str(value) for value in libgap.SmallGeneratingSet(subgroup))
    payload = json.dumps(
        {
            "sourceLabel": source_label,
            "subgroupGenerators": generators,
            "targetLabel": target_label,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def automorphism_subgroup_orbit(group, subgroup):
    automorphisms = libgap.AutomorphismGroup(group)
    generators = list(libgap.GeneratorsOfGroup(automorphisms))
    representatives = [subgroup]
    cursor = 0
    while cursor < len(representatives):
        current = representatives[cursor]
        cursor += 1
        for automorphism in generators:
            image = libgap.Image(automorphism, current)
            if not any(
                bool(libgap.IsConjugate(group, image, known))
                for known in representatives
            ):
                representatives.append(image)
                if len(representatives) > 2048:
                    raise RuntimeError("automorphism orbit exceeds exact safety bound")
    return representatives, int(libgap.Size(automorphisms)), len(generators)


def census_one(candidate: dict, isomorphism_method: str = "old") -> dict:
    source_label = str(candidate["sourceLabel"])
    target_label = str(candidate["targetLabel"])
    rejection = exact_fingerprint_rejection(candidate)
    if rejection is not None:
        return {
            **candidate,
            "actionKind": "core_free_index_24_subgroup",
            "nonisomorphismCertificate": rejection,
            "status": "certified_nonisomorphic",
        }
    source = libgap.TransitiveGroup(24, int(candidate["sourceT"]))
    target = libgap.TransitiveGroup(24, int(candidate["targetT"]))
    if isomorphism_method == "default":
        isomorphism = libgap.IsomorphismGroups(source, target)
    elif isomorphism_method == "old":
        isomorphism = ISOMORPHISM_GROUPS_OLD(source, target)
    else:
        raise ValueError(f"unknown isomorphism method: {isomorphism_method}")
    if isomorphism == libgap.fail:
        return {
            **candidate,
            "actionKind": "core_free_index_24_subgroup",
            "status": "certified_nonisomorphic",
        }

    point_stabilizer = libgap.Stabilizer(target, 1)
    subgroup = libgap.PreImage(isomorphism, point_stabilizer)
    if int(libgap.Index(source, subgroup)) != 24:
        raise ArithmeticError("isomorphism preimage does not have index 24")
    if int(libgap.Size(libgap.Core(source, subgroup))) != 1:
        raise ArithmeticError("index-24 subgroup is not core-free")
    subgroup_reps, automorphism_order, automorphism_generators = (
        automorphism_subgroup_orbit(source, subgroup)
    )

    relevant_classes = []
    source_rs = {int(value) for value in candidate["sourceR"]}
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(source)):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        source_r = fixed_points(representative)
        if source_r not in source_rs:
            continue
        relevant_classes.append(
            (class_index, int(libgap.Size(conjugacy_class)), order, source_r, representative)
        )

    subgroup_rows = []
    gold_rs = {int(value) for value in candidate["goldR"]}
    for orbit_index, representative_subgroup in enumerate(subgroup_reps):
        cosets = libgap.RightCosets(source, representative_subgroup)
        action = libgap.ActionHomomorphism(source, cosets, libgap.OnRight)
        image = libgap.Image(action)
        if int(libgap.Size(libgap.Kernel(action))) != 1:
            raise ArithmeticError("enumerated coset action is not faithful")
        actual_target_t = int(libgap.TransitiveIdentification(image))
        profiles = []
        support = []
        for class_index, class_size, order, source_r, representative in relevant_classes:
            target_r = fixed_points(libgap.Image(action, representative))
            profile = {
                "classIndex": class_index,
                "classSize": class_size,
                "order": order,
                "sourceR": source_r,
                "targetR": target_r,
            }
            profiles.append(profile)
            if actual_target_t == int(candidate["targetT"]) and target_r in gold_rs:
                support.append(profile)
        subgroup_rows.append(
            {
                "actualTargetLabel": f"24T{actual_target_t}",
                "actualTargetT": actual_target_t,
                "autOrbitIndex": orbit_index,
                "coreOrder": int(libgap.Size(libgap.Core(source, representative_subgroup))),
                "index": int(libgap.Index(source, representative_subgroup)),
                "profiles": profiles,
                "structuralGoldSupport": support,
                "subgroupClassIdentitySha256": subgroup_identity(
                    source_label, f"24T{actual_target_t}", representative_subgroup
                ),
                "subgroupOrder": int(libgap.Size(representative_subgroup)),
            }
        )
    return {
        **candidate,
        "actionKind": "core_free_index_24_subgroup",
        "automorphismGeneratorCount": automorphism_generators,
        "automorphismGroupOrder": str(automorphism_order),
        "coreFreeIndex24SubgroupClassCountForTarget": len(subgroup_rows),
        "status": "certified_isomorphic",
        "subgroupClasses": subgroup_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=6)
    parser.add_argument("--checkpoint-every", type=int, default=5)
    parser.add_argument(
        "--isomorphism-method", choices=("default", "old"), default="old"
    )
    args = parser.parse_args()
    selected = [
        row
        for index, row in enumerate(read_jsonl(args.input))
        if index % args.shard_count == args.shard_index
    ]
    rows = []
    for index, candidate in enumerate(selected, start=1):
        try:
            row = census_one(candidate, args.isomorphism_method)
        except Exception as exc:
            row = {
                **candidate,
                "actionKind": "core_free_index_24_subgroup",
                "error": f"{type(exc).__name__}: {exc}",
                "status": "error",
            }
        rows.append(row)
        if args.checkpoint_every and index % args.checkpoint_every == 0:
            write_jsonl(args.output, rows)
            print(json.dumps({"completed": index, "shard": args.shard_index, "total": len(selected)}), flush=True)
            if len(selected) == 1:
                # The crash-isolated driver deliberately gives this worker one
                # candidate.  Exit at the first durable checkpoint, before GAP
                # can hang while releasing the completed isomorphism objects.
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(0 if row["status"] != "error" else 2)
    write_jsonl(args.output, rows)
    errors = sum(row["status"] == "error" for row in rows)
    print(json.dumps({"errors": errors, "rows": len(rows), "shard": args.shard_index}))
    return 0 if not errors else 2


if __name__ == "__main__":
    # Some GAP isomorphism paths finish the exact row and then hang while the
    # embedded interpreter tears down.  Every output/checkpoint above is
    # atomically closed before returning, so bypass GAP finalization in these
    # crash-isolated one-row workers after flushing the progress stream.
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
