#!/usr/bin/env sage -python
"""Exact GAP permutation-character audit for five F6 multi-orbit packets."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
CAMPAIGN = ROOT / "data" / "campaign_20260727_f627"
CANDIDATES = CAMPAIGN / "f6_post22_multi_wave_candidates.jsonl"
OUTPUT = CAMPAIGN / "f6_post22_multi_permutation_character_audit.json"
SOURCE_PAIRS = [
    ("24T3698", 8),
    ("24T3721", 8),
    ("24T3818", 8),
    ("24T8353", 0),
    ("24T15342", 16),
]
POINTS = libgap.eval("[1..24]")


def cycle_type(permutation) -> tuple[int, ...]:
    return tuple(
        sorted(int(value) for value in libgap.CycleLengths(permutation, POINTS))
    )


def sha256_json(value) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def atomic_new(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    packets = {
        (str(row["sourceLabel"]), int(row["sourceR"])): row
        for row in (
            json.loads(line)
            for line in CANDIDATES.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    results = []
    for source_label, source_r in SOURCE_PAIRS:
        packet = packets[(source_label, source_r)]
        source_t = int(source_label.removeprefix("24T"))
        group = libgap.TransitiveGroup(24, source_t)
        pair_orbits = list(libgap.Orbits(group, libgap.Combinations(POINTS, 2), libgap.OnSets))
        slots = list(packet["orbitTargets"])
        homomorphisms = []
        verified_slots = []
        for position, slot in enumerate(slots):
            orbit_index = int(slot["orbitIndex"])
            orbit = pair_orbits[orbit_index]
            hom = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
            image = libgap.Image(hom)
            label = f"24T{int(libgap.TransitiveIdentification(image))}"
            if (
                int(libgap.Length(orbit)) != 24
                or label != str(slot["targetLabel"])
                or int(libgap.Size(libgap.Kernel(hom))) != 1
            ):
                raise ValueError(f"bad target slot in {source_label}: {slot}")
            homomorphisms.append(hom)
            verified_slots.append(
                {
                    "slotPosition": position,
                    "orbitIndex": orbit_index,
                    "targetLabel": label,
                    "targetT": int(slot["targetT"]),
                }
            )

        classes = list(libgap.ConjugacyClasses(group))
        class_rows = []
        characters = [[] for _ in homomorphisms]
        natural_type_counts = {}
        temporary = []
        for class_index, conjugacy_class in enumerate(classes, 1):
            representative = libgap.Representative(conjugacy_class)
            natural = cycle_type(representative)
            natural_type_counts[natural] = natural_type_counts.get(natural, 0) + 1
            action_types = [
                cycle_type(libgap.Image(hom, representative))
                for hom in homomorphisms
            ]
            fixed_points = [pattern.count(1) for pattern in action_types]
            for position, value in enumerate(fixed_points):
                characters[position].append(value)
            temporary.append(
                (
                    class_index,
                    int(libgap.Size(conjugacy_class)),
                    int(libgap.Order(representative)),
                    natural,
                    action_types,
                    fixed_points,
                )
            )
        for (
            class_index,
            class_size,
            order,
            natural,
            action_types,
            fixed_points,
        ) in temporary:
            class_rows.append(
                {
                    "classIndex": class_index,
                    "classSize": class_size,
                    "order": order,
                    "naturalCycleType": list(natural),
                    "naturalCycleTypeClassMultiplicity": natural_type_counts[natural],
                    "actionCycleTypes": [list(value) for value in action_types],
                    "permutationCharacterValues": fixed_points,
                }
            )

        equivalence_groups = {}
        for position, character in enumerate(characters):
            digest = sha256_json(character)
            equivalence_groups.setdefault(digest, []).append(position)
        target_labels = [str(slot["targetLabel"]) for slot in slots]
        desired_labels = {
            str(route["targetLabel"])
            for route in packet.get("orbitTargets") or []
            if str(route["targetLabel"]) in {
                "24T3836",
                "24T3899",
                "24T3894",
                "24T8253",
                "24T15962",
            }
        }
        if len(desired_labels) != 1:
            raise ValueError(f"cannot identify desired target for {source_label}")
        desired_label = next(iter(desired_labels))
        desired_positions = [
            index for index, label in enumerate(target_labels) if label == desired_label
        ]
        if len(desired_positions) != 1:
            raise ValueError(f"desired target is not a unique slot for {source_label}")
        desired_position = desired_positions[0]
        other_positions = [
            index for index in range(len(slots)) if index != desired_position
        ]
        gassmann_collisions = [
            position
            for position in other_positions
            if characters[position] == characters[desired_position]
            and target_labels[position] != desired_label
        ]
        separating = []
        for class_row in class_rows:
            values = class_row["permutationCharacterValues"]
            if all(
                values[desired_position] != values[position]
                for position in other_positions
            ):
                separating.append(class_row)
        separating.sort(
            key=lambda row: (
                row["naturalCycleTypeClassMultiplicity"] != 1,
                -row["classSize"],
                row["classIndex"],
            )
        )
        results.append(
            {
                "sourceLabel": source_label,
                "sourceR": source_r,
                "sourceSubmissionId": str(packet["sourceSubmissionId"]),
                "sourcePolynomialIndex": int(packet["sourcePolynomialIndex"]),
                "sourceCoefficientSha256": str(
                    packet["sourceCoefficientSha256"]
                ),
                "verifiedSlots": verified_slots,
                "desiredTargetLabel": desired_label,
                "desiredSlotPosition": desired_position,
                "sourceConjugacyClassCount": len(classes),
                "permutationCharacters": [
                    {
                        "slotPosition": position,
                        "targetLabel": target_labels[position],
                        "values": character,
                        "sha256": sha256_json(character),
                    }
                    for position, character in enumerate(characters)
                ],
                "characterEquivalenceGroups": [
                    {
                        "characterSha256": digest,
                        "slotPositions": positions,
                        "targetLabels": [target_labels[p] for p in positions],
                    }
                    for digest, positions in sorted(equivalence_groups.items())
                ],
                "desiredTargetGassmannCollisionSlots": gassmann_collisions,
                "gassmannBlocked": bool(gassmann_collisions),
                "separatingClassCount": len(separating),
                "separatingClasses": separating,
                "allClasses": class_rows,
            }
        )
    summary = {
        "schemaVersion": "f6-post22-multi-permutation-character-audit-v1",
        "method": "exact-gap-permutation-character-comparison",
        "routes": len(results),
        "gassmannBlocked": sum(row["gassmannBlocked"] for row in results),
        "characterSeparated": sum(not row["gassmannBlocked"] for row in results),
        "rows": results,
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    payload = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
    atomic_new(OUTPUT, payload)
    print(
        json.dumps(
            {
                "output": str(OUTPUT.relative_to(ROOT)),
                "outputSha256": hashlib.sha256(payload).hexdigest(),
                "gassmannBlocked": summary["gassmannBlocked"],
                "characterSeparated": summary["characterSeparated"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
