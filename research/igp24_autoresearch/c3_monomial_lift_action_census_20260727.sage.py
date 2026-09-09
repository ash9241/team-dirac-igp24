#!/usr/bin/env sage
"""Census the generic degree-24 action of q(x^3) for local degree-8 sources."""

import json
import sqlite3
from pathlib import Path

from sage.libs.gap.libgap import libgap


ROOT = Path.cwd()
SOURCES = ROOT / "data" / "agent_non12_all_degree8_subfields.jsonl"
LEDGER = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "c3_monomial_lift_action_census_20260727.json"


def perm_from_images(images):
    return libgap.PermList(libgap(images))


def lifted_c3_group(source_t):
    h_group = libgap.TransitiveGroup(8, source_t)
    generators = []

    # Independent cube-root rotations in each of the eight blocks.
    for block in range(1, 9):
        images = list(range(1, 25))
        start = 3 * (block - 1)
        images[start] = start + 2
        images[start + 1] = start + 3
        images[start + 2] = start + 1
        generators.append(perm_from_images(images))

    # Lift the degree-8 source action while preserving the cube-root index.
    for h_generator in list(libgap.GeneratorsOfGroup(h_group)):
        images = []
        for block in range(1, 9):
            image_block = int(libgap.OnPoints(block, h_generator))
            for root_index in range(3):
                images.append(3 * (image_block - 1) + root_index + 1)
        generators.append(perm_from_images(images))

    # The rational cyclotomic involution simultaneously inverts every C3.
    images = []
    for block in range(1, 9):
        start = 3 * (block - 1)
        images.extend([start + 1, start + 3, start + 2])
    generators.append(perm_from_images(images))
    return libgap.Group(generators)


source_rows = [
    json.loads(line)
    for line in SOURCES.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
source_classes = sorted(
    {
        (int(row["galoisGroup"]["label"].split("T", 1)[1]), int(row["realRoots"]))
        for row in source_rows
    }
)

with sqlite3.connect(LEDGER) as connection:
    target_rows = {
        (row[0], int(row[1])): {
            "teamCount": int(row[2]),
            "discovered": int(row[3]),
            "generatedAt": row[4],
        }
        for row in connection.execute(
            "SELECT label,r,team_count,discovered,generated_at FROM targets"
        )
    }

action_cache = {}
rows = []
for source_t, source_r in source_classes:
    if source_t not in action_cache:
        group = lifted_c3_group(source_t)
        action_cache[source_t] = {
            "targetT": int(libgap.TransitiveIdentification(group)),
            "order": int(libgap.Size(group)),
        }
    action = action_cache[source_t]
    target_label = f"24T{action['targetT']}"
    target = target_rows.get((target_label, source_r))
    rows.append(
        {
            "sourceLabel": f"8T{source_t}",
            "sourceR": source_r,
            "targetLabel": target_label,
            "targetR": source_r,
            "targetOrder": action["order"],
            "target": target,
            "currentTc0": bool(
                target
                and target["teamCount"] == 0
                and target["discovered"] == 0
            ),
        }
    )

payload = {
    "schemaVersion": 1,
    "mechanism": (
        "generic q(x^3): C3^8 semidirect source group, extended by the "
        "diagonal cyclotomic involution"
    ),
    "sourceGroups": len(action_cache),
    "sourceClasses": len(source_classes),
    "rows": rows,
    "currentTc0Rows": [row for row in rows if row["currentTc0"]],
    "networkCalls": 0,
    "submissionCalls": 0,
}
OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            "output": str(OUTPUT),
            "sourceGroups": len(action_cache),
            "sourceClasses": len(source_classes),
            "currentTc0Rows": len(payload["currentTc0Rows"]),
        },
        sort_keys=True,
    )
)
