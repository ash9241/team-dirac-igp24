#!/usr/bin/env sage -python
"""Exact permutation-character dispatcher for recursive F6 multi-orbit packets."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
CAMPAIGN = ROOT / "data" / "campaign_20260727_f627"
CENSUS = CAMPAIGN / "f6_recursive_16948_16949_census.jsonl"
OUTPUT = CAMPAIGN / "f6_recursive_16948_16949_character_audit.json"
POINTS = libgap.eval("[1..24]")


def cycle_type(permutation) -> tuple[int, ...]:
    return tuple(
        sorted(int(value) for value in libgap.CycleLengths(permutation, POINTS))
    )


def sha_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


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
    rows = [
        json.loads(line)
        for line in CENSUS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    audits = []
    for census in rows:
        group = libgap.TransitiveGroup(24, int(census["sourceT"]))
        pair_orbits = list(
            libgap.Orbits(group, libgap.Combinations(POINTS, 2), libgap.OnSets)
        )
        slots = sorted(census["targets"], key=lambda row: int(row["orbitIndex"]))
        homomorphisms = []
        for slot in slots:
            orbit = pair_orbits[int(slot["orbitIndex"])]
            hom = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
            image = libgap.Image(hom)
            if (
                int(libgap.Length(orbit)) != 24
                or int(libgap.Size(libgap.Kernel(hom))) != 1
                or int(libgap.TransitiveIdentification(image))
                != int(slot["targetT"])
            ):
                raise ValueError("census target slot failed exact GAP reproduction")
            homomorphisms.append(hom)

        temporary = []
        multiplicities = {}
        characters = [[] for _ in slots]
        for class_index, conjugacy_class in enumerate(
            libgap.ConjugacyClasses(group), 1
        ):
            representative = libgap.Representative(conjugacy_class)
            natural = cycle_type(representative)
            multiplicities[natural] = multiplicities.get(natural, 0) + 1
            action_types = [
                cycle_type(libgap.Image(hom, representative))
                for hom in homomorphisms
            ]
            for position, action_type in enumerate(action_types):
                characters[position].append(action_type.count(1))
            temporary.append(
                {
                    "classIndex": class_index,
                    "classSize": int(libgap.Size(conjugacy_class)),
                    "order": int(libgap.Order(representative)),
                    "naturalCycleType": list(natural),
                    "actionCycleTypes": [list(value) for value in action_types],
                    "permutationCharacterValues": [
                        value.count(1) for value in action_types
                    ],
                }
            )
        class_rows = []
        separating = []
        for row in temporary:
            natural = tuple(row["naturalCycleType"])
            row["naturalCycleTypeClassMultiplicity"] = multiplicities[natural]
            class_rows.append(row)
            action_types = {
                tuple(value) for value in row["actionCycleTypes"]
            }
            if multiplicities[natural] == 1 and len(action_types) == len(slots):
                separating.append(row)
        separating.sort(key=lambda row: (-row["classSize"], row["classIndex"]))

        equivalence = {}
        for position, character in enumerate(characters):
            equivalence.setdefault(sha_json(character), []).append(position)
        audits.append(
            {
                "sourceLabel": str(census["sourceLabel"]),
                "sourceR": 16,
                "sourceT": int(census["sourceT"]),
                "censusExactCertificateSha256": str(
                    census["exactCertificateSha256"]
                ),
                "slots": [
                    {
                        "slotPosition": position,
                        "orbitIndex": int(slot["orbitIndex"]),
                        "targetLabel": str(slot["targetLabel"]),
                        "targetT": int(slot["targetT"]),
                    }
                    for position, slot in enumerate(slots)
                ],
                "characterEquivalenceGroups": [
                    {
                        "characterSha256": digest,
                        "slotPositions": positions,
                        "targetLabels": [
                            str(slots[position]["targetLabel"])
                            for position in positions
                        ],
                    }
                    for digest, positions in sorted(equivalence.items())
                ],
                "gassmannBlocked": any(
                    len(positions) > 1 for positions in equivalence.values()
                ),
                "singlePrimeFullAssignmentClassCount": len(separating),
                "singlePrimeFullAssignmentClasses": separating,
                "allClasses": class_rows,
            }
        )
    summary = {
        "schemaVersion": "f6-recursive-character-dispatch-v1",
        "method": "exact GAP permutation characters and cycle types",
        "rows": audits,
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
                "sources": [
                    {
                        "label": row["sourceLabel"],
                        "gassmannBlocked": row["gassmannBlocked"],
                        "singlePrimeFullAssignmentClassCount": row[
                            "singlePrimeFullAssignmentClassCount"
                        ],
                    }
                    for row in audits
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
