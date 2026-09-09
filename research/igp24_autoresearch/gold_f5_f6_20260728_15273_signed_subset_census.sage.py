#!/usr/bin/env sage -python
"""Exact signed-subset action census for the new 24T15273/r20 source.

This is an isolated, action-only audit.  It enumerates every fixed-point-free
centralizer block system of 24T15273, every quotient k-subset orbit of length
12, and the resulting signed degree-24 action.  Current target state is read
from the local ledger in read-only mode; no coefficient material is used.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
from collections import defaultdict, deque
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
OUTPUT = DATA / "gold_f5_f6_20260728_15273_signed_subset_census.json"
SOURCE_T = 15273
RESERVED = {
    ("24T10482", 8),
    ("24T14293", 16),
    ("24T16948", 16),
    ("24T16949", 16),
    ("24T15337", 20),
    ("24T15043", 4),
    ("24T11787", 12),
    ("24T24877", 14),
    ("24T15273", 20),
}


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def subset_orbits(generators: list[list[int]], size: int):
    remaining = set(itertools.combinations(range(12), size))
    orbits = []
    while remaining:
        start = min(remaining)
        orbit = {start}
        queue = deque([start])
        while queue:
            subset = queue.popleft()
            for permutation in generators:
                image = tuple(sorted(permutation[index] for index in subset))
                if image not in orbit:
                    orbit.add(image)
                    queue.append(image)
        remaining.difference_update(orbit)
        orbits.append(sorted(orbit))
    return orbits


def blocks_for_flip(flip) -> list[tuple[int, int]]:
    return [
        (point, int(libgap.OnPoints(point, flip)))
        for point in range(1, 25)
        if point < int(libgap.OnPoints(point, flip))
    ]


def block_action(blocks: list[tuple[int, int]], permutation):
    point_to_block = {}
    point_sign = {}
    for index, block in enumerate(blocks):
        point_to_block[block[0]] = index
        point_to_block[block[1]] = index
        point_sign[block[0]] = 0
        point_sign[block[1]] = 1
    block_permutation = []
    signs = []
    for block in blocks:
        image = int(libgap.OnPoints(block[0], permutation))
        block_permutation.append(point_to_block[image])
        signs.append(point_sign[image])
    return block_permutation, signs


def induced_permutation(orbit, block_permutation, signs):
    positions = {subset: index for index, subset in enumerate(orbit)}
    images = []
    for subset in orbit:
        image_subset = tuple(sorted(block_permutation[index] for index in subset))
        target = positions[image_subset]
        sign = sum(signs[index] for index in subset) % 2
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def fixed_points(permutation) -> int:
    return 24 - int(libgap.NrMovedPoints(permutation))


def target_state(connection: sqlite3.Connection, label: str, signature: int) -> dict:
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
          EXISTS(SELECT 1 FROM baseline_pairs b
                 WHERE b.label=t.label AND b.r=t.r),
          EXISTS(SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1)
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (label, signature),
    ).fetchone()
    if row is None:
        return {"present": False}
    state = {
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "generatedAt": str(row[3]),
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "present": True,
        "teamCount": int(row[0]),
    }
    state["tc0Unreserved"] = (
        state["teamCount"] == 0
        and not state["discovered"]
        and not state["baseline"]
        and not state["owned"]
        and (label, signature) not in RESERVED
    )
    return state


def main() -> int:
    if OUTPUT.exists() or Path(str(OUTPUT) + ".tmp").exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT}")
    group = libgap.TransitiveGroup(24, SOURCE_T)
    generators = list(libgap.GeneratorsOfGroup(group))
    relevant_classes = []
    for index, conjugacy_class in enumerate(libgap.ConjugacyClasses(group)):
        representative = libgap.Representative(conjugacy_class)
        if int(libgap.Order(representative)) in (1, 2):
            relevant_classes.append(
                {
                    "classIndex": index,
                    "classSize": int(libgap.Size(conjugacy_class)),
                    "representative": representative,
                    "sourceR": fixed_points(representative),
                }
            )

    centralizer = libgap.Centralizer(libgap.SymmetricGroup(24), group)
    systems = {}
    for flip in libgap.Elements(centralizer):
        if int(libgap.Order(flip)) != 2 or int(libgap.NrMovedPoints(flip)) != 24:
            continue
        blocks = blocks_for_flip(flip)
        digest = hashlib.sha256(canonical_json(blocks).encode()).hexdigest()
        systems[digest] = blocks

    rows = []
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        for system_sha, blocks in sorted(systems.items()):
            generator_data = [block_action(blocks, generator) for generator in generators]
            quotient_generators = [
                libgap.PermList([value + 1 for value in block_permutation])
                for block_permutation, _ in generator_data
            ]
            quotient = libgap.Group(quotient_generators)
            for subset_size in range(1, 12):
                for orbit_index, orbit in enumerate(
                    subset_orbits(
                        [block_permutation for block_permutation, _ in generator_data],
                        subset_size,
                    )
                ):
                    if len(orbit) != 12:
                        continue
                    induced_generators = [
                        induced_permutation(orbit, block_permutation, signs)
                        for block_permutation, signs in generator_data
                    ]
                    action = libgap.Group(induced_generators)
                    if not bool(libgap.IsTransitive(action, libgap.eval("[1..24]"))):
                        continue
                    target_t = int(libgap.TransitiveIdentification(action))
                    target_label = f"24T{target_t}"
                    signature_map = defaultdict(set)
                    profiles = []
                    for class_row in relevant_classes:
                        block_permutation, signs = block_action(
                            blocks, class_row["representative"]
                        )
                        induced = induced_permutation(
                            orbit, block_permutation, signs
                        )
                        target_r = fixed_points(induced)
                        signature_map[class_row["sourceR"]].add(target_r)
                        profiles.append(
                            {
                                "classIndex": class_row["classIndex"],
                                "classSize": class_row["classSize"],
                                "sourceR": class_row["sourceR"],
                                "targetR": target_r,
                            }
                        )
                    source_r20_targets = sorted(signature_map.get(20, set()))
                    states = {
                        str(signature): target_state(
                            connection, target_label, signature
                        )
                        for signature in source_r20_targets
                    }
                    rows.append(
                        {
                            "blockActionOrder": int(libgap.Size(quotient)),
                            "blockActionT12": int(
                                libgap.TransitiveIdentification(quotient)
                            ),
                            "blockSystemSha256": system_sha,
                            "orbitIndex": orbit_index,
                            "profiles": profiles,
                            "sourceLabel": "24T15273",
                            "sourceR": 20,
                            "sourceR20TargetSignatures": source_r20_targets,
                            "subsetOrbit": [list(subset) for subset in orbit],
                            "subsetSize": subset_size,
                            "targetLabel": target_label,
                            "targetOrder": int(libgap.Size(action)),
                            "targetStates": states,
                            "targetT": target_t,
                            "tc0UnreservedSignatures": [
                                signature
                                for signature in source_r20_targets
                                if states[str(signature)].get("tc0Unreserved")
                            ],
                        }
                    )

    payload = {
        "actionOnly": True,
        "blockSystemCount": len(systems),
        "currentTc0UnreservedRouteCount": sum(
            bool(row["tc0UnreservedSignatures"]) for row in rows
        ),
        "currentTc0UnreservedRoutes": [
            row for row in rows if row["tc0UnreservedSignatures"]
        ],
        "degree24ActionCount": len(rows),
        "networkCalls": 0,
        "rows": rows,
        "schemaVersion": "gold-f5-f6-15273-signed-subset-census-v1",
        "sourceLabel": "24T15273",
        "sourceR": 20,
        "submissionCalls": 0,
    }
    temporary = Path(str(OUTPUT) + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(OUTPUT)
    print(
        json.dumps(
            {
                "actionCount": len(rows),
                "blockSystemCount": len(systems),
                "currentTc0UnreservedRouteCount": payload[
                    "currentTc0UnreservedRouteCount"
                ],
                "output": str(OUTPUT),
                "targets": sorted(
                    {
                        (
                            row["targetLabel"],
                            tuple(row["tc0UnreservedSignatures"]),
                        )
                        for row in rows
                        if row["tc0UnreservedSignatures"]
                    }
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
