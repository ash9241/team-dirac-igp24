#!/usr/bin/env sage
"""Exact structural gate for 8T22 octic fibers over cubic bases.

The literature family x^8 + a*x^4 + b realizes the extraspecial order-32
group 8T22 under explicit square/non-square conditions.  Norming such an
octic over a cubic base gives a natural three-block degree-24 action.  This
script identifies the generic full wreath actions for cyclic and S3 cubic
closures and intersects their exact involution signatures with scoreable tc0.

No arithmetic, network, or submission call is made.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from sage.libs.gap.libgap import libgap


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "m30_8t22_octic_wreath_gate_20260727.json"


def perm(images):
    return libgap.PermList(libgap(images))


def lift_fiber(element, block):
    images = list(range(1, 25))
    for point in range(1, 9):
        image = int(libgap.OnPoints(point, element))
        images[8 * block + point - 1] = 8 * block + image
    return perm(images)


def block_perm(block_images):
    return perm(
        [
            8 * block_images[block] + point
            for block in range(3)
            for point in range(1, 9)
        ]
    )


def fixed_points(element):
    return sum(
        int(libgap.OnPoints(point, element)) == point for point in range(1, 25)
    )


def frontier(connection):
    return {
        (str(label), int(r))
        for label, r in connection.execute(
            """
            SELECT t.label,t.r FROM targets t
            WHERE t.team_count=0 AND t.discovered=0
              AND NOT EXISTS (
                SELECT 1 FROM baseline_pairs b
                WHERE b.label=t.label AND b.r=t.r
              )
              AND NOT EXISTS (
                SELECT 1 FROM verifications v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              )
            """
        )
    }


def identify(outer, scoreable):
    fiber = libgap.TransitiveGroup(8, 22)
    generators = []
    for block in range(3):
        for generator in list(libgap.GeneratorsOfGroup(fiber)):
            generators.append(lift_fiber(generator, block))
    generators.append(block_perm([1, 2, 0]))
    if outer == "S3":
        generators.append(block_perm([1, 0, 2]))
    group = libgap.Group(generators)
    expected = (32**3) * (3 if outer == "C3" else 6)
    order = int(libgap.Size(group))
    if order != expected or not bool(libgap.IsTransitive(group)):
        raise ArithmeticError("full wreath construction failed")
    label = f"24T{int(libgap.TransitiveIdentification(group))}"
    signatures = sorted(
        {
            fixed_points(libgap.Representative(conjugacy_class))
            for conjugacy_class in list(libgap.ConjugacyClasses(group))
            if int(libgap.Order(libgap.Representative(conjugacy_class))) in (1, 2)
        }
    )
    return {
        "outer": outer,
        "order": order,
        "signatures": signatures,
        "targetLabel": label,
        "tc0ScoreableSignatures": [
            r for r in signatures if (label, int(r)) in scoreable
        ],
    }


def main():
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    scoreable = frontier(connection)
    rows = [identify("C3", scoreable), identify("S3", scoreable)]
    live_pairs = sorted(
        {
            f"{row['targetLabel']}/r{r}"
            for row in rows
            for r in row["tc0ScoreableSignatures"]
        }
    )
    payload = {
        "actionFamily": "full 8T22 wreath cubic outer action",
        "frontierGeneratedAt": connection.execute(
            "SELECT MAX(generated_at) FROM targets"
        ).fetchone()[0],
        "frontierPairs": len(scoreable),
        "literatureModel": "x^8+a*x^4+b with b square and sqrt(b) nonsquare",
        "livePairs": live_pairs,
        "networkCalls": 0,
        "rows": rows,
        "status": (
            "promote_to_arithmetic"
            if live_pairs
            else "blocked_generic_full_wreath_zero_tc0"
        ),
        "submissionCalls": 0,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "livePairs": live_pairs,
                "output": str(OUTPUT),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "status": payload["status"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
