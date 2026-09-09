#!/usr/bin/env sage
"""Census shared-quadratic (4Tj wr C2) x_C2 (3Tk wr C2) actions."""

import json
import sqlite3
from pathlib import Path

from sage.all import SymmetricGroup, TransitiveGroup
from sage.libs.gap.libgap import libgap


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
OUT = ROOT / "data" / "n27_shared_quadratic_family_census_20260727.json"


def embedded_images(permutation, degree, block, total):
    images = list(range(total))
    offset = block * degree
    for i in range(degree):
        images[offset + i] = offset + int(permutation(i + 1)) - 1
    return images


def compatible_action(t4, t3):
    g4 = TransitiveGroup(4, t4)
    g3 = TransitiveGroup(3, t3)
    points = [
        (i, j)
        for i in range(8)
        for j in range(6)
        if i // 4 == j // 3
    ]
    point_index = {point: k + 1 for k, point in enumerate(points)}

    generators = []
    id8 = list(range(8))
    id6 = list(range(6))
    for generator in g4.gens():
        for block in range(2):
            generators.append((embedded_images(generator, 4, block, 8), id6))
    for generator in g3.gens():
        for block in range(2):
            generators.append((id8, embedded_images(generator, 3, block, 6)))
    swap8 = list(range(4, 8)) + list(range(4))
    swap6 = list(range(3, 6)) + list(range(3))
    generators.append((swap8, swap6))

    s24 = SymmetricGroup(24)
    action_generators = []
    for a, b in generators:
        images = [point_index[(a[i], b[j])] for i, j in points]
        action_generators.append(s24(images))
    group = s24.subgroup(action_generators)
    expected_order = 2 * int(g4.order()) ** 2 * int(g3.order()) ** 2
    if not group.is_transitive() or int(group.order()) != expected_order:
        raise ArithmeticError("bad compatible action")
    return {
        "t4": t4,
        "g4": "4T%d" % t4,
        "g4_order": int(g4.order()),
        "t3": t3,
        "g3": "3T%d" % t3,
        "g3_order": int(g3.order()),
        "label": "24T%d" % int(libgap.TransitiveIdentification(group)),
        "order": int(group.order()),
    }


def main():
    rows = [compatible_action(t4, t3) for t4 in range(1, 6) for t3 in range(1, 3)]
    conn = sqlite3.connect(DB)
    for row in rows:
        row["targets"] = [
            {
                "r": int(r),
                "team_count": int(team_count),
                "discovered": int(discovered),
                "minimum_disc_abs": minimum_disc_abs,
                "generated_at": generated_at,
            }
            for r, team_count, discovered, minimum_disc_abs, generated_at in conn.execute(
                """
                SELECT r,team_count,discovered,minimum_disc_abs,generated_at
                FROM targets WHERE label=? ORDER BY r
                """,
                (row["label"],),
            )
        ]
        row["gold_signatures"] = [
            target["r"]
            for target in row["targets"]
            if target["team_count"] == 0 and target["discovered"] == 0
        ]
    conn.close()
    payload = {
        "family": "shared_quadratic_fiber_products",
        "count": len(rows),
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
