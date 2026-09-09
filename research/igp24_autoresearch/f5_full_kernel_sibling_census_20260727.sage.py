#!/usr/bin/env sage -python
"""Identify the generic full-Kummer siblings of current F5 action routes.

This is a coefficient-free exact group census.  For a 12-point quotient
action H and a length-12 orbit O of unordered pairs, it constructs the action
of C2^12 : H on the 24 signed symbols +/-sqrt(y_i*y_j), (i,j) in O.  The
base-group image is generated explicitly by the twelve incidence vectors, so
the resulting transitive-group identification is exact.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import PermutationGroup, SymmetricGroup, TransitiveGroup


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
OUTPUT = DATA / "f5_full_kernel_sibling_census_20260727.json"


def stable_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def signed_pair_group(
    source_t: int,
    quotient_t: int,
    block_system: list[list[int]],
    orbit: list[list[int]],
):
    pairs = [tuple(sorted(map(int, pair))) for pair in orbit]
    if len(pairs) != 12 or len(set(pairs)) != 12:
        raise ValueError("expected twelve distinct unordered pairs")
    pair_index = {pair: index for index, pair in enumerate(pairs)}
    source = TransitiveGroup(24, source_t)
    block_symmetric = SymmetricGroup(12)
    element_to_block = {
        int(element): block_index
        for block_index, block in enumerate(block_system)
        for element in block
    }
    if len(element_to_block) != 24:
        raise ValueError("source block system is not a partition of 24 points")
    quotient_generators = []
    for source_generator in source.gens():
        images = []
        for block in block_system:
            image_element = int(source_generator(int(block[0])))
            images.append(element_to_block[image_element] + 1)
        quotient_generators.append(block_symmetric(images))
    quotient = PermutationGroup(quotient_generators)
    if int(quotient._gap_().TransitiveIdentification()) != quotient_t:
        raise ValueError("source block action does not have the sealed quotient label")
    symmetric = SymmetricGroup(24)
    generators = []

    for quotient_generator in quotient.gens():
        images = []
        for pair in pairs:
            image_pair = tuple(
                sorted(
                    (
                        int(quotient_generator(pair[0] + 1)) - 1,
                        int(quotient_generator(pair[1] + 1)) - 1,
                    )
                )
            )
            image_index = pair_index[image_pair]
            images.extend((2 * image_index + 1, 2 * image_index + 2))
        generators.append(symmetric(images))

    for coordinate in range(12):
        images = list(range(1, 25))
        for pair_index_value, pair in enumerate(pairs):
            if coordinate in pair:
                left = 2 * pair_index_value
                images[left], images[left + 1] = images[left + 1], images[left]
        generators.append(symmetric(images))

    return quotient, PermutationGroup(generators)


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    current_gold = {
        (str(row["label"]), int(row["r"]))
        for row in connection.execute(
            """
            SELECT t.label,t.r
            FROM targets t
            LEFT JOIN baseline_pairs b ON b.label=t.label AND b.r=t.r
            WHERE t.team_count=0 AND t.discovered=0 AND b.label IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM verifications v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              )
            """
        )
    }
    target_labels_with_gold = {label for label, _ in current_gold}

    actions = {}
    input_paths = sorted(DATA.glob("agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl"))
    for path in input_paths:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row["targetLabel"]) not in target_labels_with_gold:
                continue
            key_value = {
                "pairOrbit": row["pairOrbit"],
                "quotientT12": int(row["quotientT12"]),
            }
            key = stable_hash(key_value)
            actions.setdefault(key, row)

    rows = []
    for key, action in sorted(actions.items()):
        quotient, group = signed_pair_group(
            int(action["sourceT"]),
            int(action["quotientT12"]),
            action["sourceBlockSystem"],
            action["pairOrbit"],
        )
        if not group.is_transitive():
            raise ValueError("constructed signed-pair action is intransitive")
        full_t = int(group._gap_().TransitiveIdentification())
        full_label = f"24T{full_t}"
        full_gold_r = sorted(r for label, r in current_gold if label == full_label)
        rows.append(
            {
                "actionSha256": key,
                "fullKernelLabel": full_label,
                "fullKernelOrder": int(group.order()),
                "fullKernelTc0R": full_gold_r,
                "pairOrbit": action["pairOrbit"],
                "quotientOrder": int(quotient.order()),
                "quotientT12": int(action["quotientT12"]),
                "relationKernelLabel": str(action["targetLabel"]),
                "relationKernelOrder": int(action["targetOrder"]),
                "sourceLabel": str(action["sourceLabel"]),
            }
        )

    result = {
        "actionCount": len(rows),
        "currentGoldPairCount": len(current_gold),
        "exactConstruction": "C2^12:H acting on signed length-12 unordered-pair orbit",
        "inputFiles": [
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in input_paths
        ],
        "rows": rows,
        "rowsWithCurrentGoldSibling": sum(bool(row["fullKernelTc0R"]) for row in rows),
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "actionCount": result["actionCount"],
                "output": str(OUTPUT.relative_to(ROOT)),
                "rowsWithCurrentGoldSibling": result["rowsWithCurrentGoldSibling"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
