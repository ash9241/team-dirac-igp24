#!/usr/bin/env sage
"""Exact action gate for a rank-eight Kummer lift over a cyclic cubic.

Let a cyclic cubic group permute three blocks of eight vertices.  The full
multiquadratic kernel is F_2^9, acting by translations on the three affine
3-cubes.  A single cyclically invariant Kummer relation cuts out a hyperplane
of order 2^8.  This script identifies every nonzero invariant hyperplane in
the degree-24 transitive-group library, computes all complex-conjugation fixed
point counts, and intersects them with the frozen scoreable tc0 frontier.

It performs no network or submission calls.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import GF, Matrix, VectorSpace
from sage.libs.gap.libgap import libgap


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "m28_cyclic_cubic_rank8_kummer_census_20260727.json"


def permutation(images):
    return libgap.PermList(libgap(images))


def translation(vector):
    """Translation by three 3-bit masks on three affine 3-cubes."""
    masks = []
    for block in range(3):
        mask = sum(int(vector[3 * block + bit]) << bit for bit in range(3))
        masks.append(mask)
    images = []
    for block in range(3):
        for vertex in range(8):
            images.append(8 * block + (vertex ^ masks[block]) + 1)
    return permutation(images)


def cyclic_block_permutation():
    images = []
    for block in range(3):
        for vertex in range(8):
            images.append(8 * ((block + 1) % 3) + vertex + 1)
    return permutation(images)


def fixed_points(element):
    return sum(
        int(libgap.OnPoints(point, element)) == point for point in range(1, 25)
    )


def relation_kernel_basis(linear_form):
    field = GF(2)
    ambient = VectorSpace(field, 9)
    rows = []
    for vector in ambient:
        value = field(0)
        for block in range(3):
            for bit in range(3):
                value += field(linear_form[bit]) * vector[3 * block + bit]
        if value == 0:
            rows.append(vector)
    matrix = Matrix(field, rows)
    basis = matrix.row_space().basis()
    if len(basis) != 8:
        raise ArithmeticError("invariant relation did not cut out a hyperplane")
    return basis


def scoreable_frontier(connection):
    return {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            """
            SELECT t.label,t.r
            FROM targets t
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


def main():
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    frontier = scoreable_frontier(connection)
    generated_at = connection.execute(
        "SELECT MAX(generated_at) FROM targets"
    ).fetchone()[0]

    rows = []
    for encoded_form in range(1, 8):
        form = [(encoded_form >> bit) & 1 for bit in range(3)]
        basis = relation_kernel_basis(form)
        generators = [translation(vector) for vector in basis]
        generators.append(cyclic_block_permutation())
        group = libgap.Group(generators)
        order = int(libgap.Size(group))
        if order != 768 or not bool(libgap.IsTransitive(group)):
            raise ArithmeticError("rank-eight Kummer group failed exact order/action gate")
        target_t = int(libgap.TransitiveIdentification(group))
        target_label = f"24T{target_t}"
        signatures = sorted(
            {
                fixed_points(libgap.Representative(conjugacy_class))
                for conjugacy_class in list(libgap.ConjugacyClasses(group))
                if int(
                    libgap.Order(libgap.Representative(conjugacy_class))
                )
                in (1, 2)
            }
        )
        live = sorted(
            r for r in signatures if (target_label, int(r)) in frontier
        )
        rows.append(
            {
                "invariantLinearForm": form,
                "kernelDimension": len(basis),
                "order": order,
                "signatures": signatures,
                "targetLabel": target_label,
                "tc0ScoreableSignatures": live,
            }
        )

    labels = sorted({row["targetLabel"] for row in rows})
    live_pairs = sorted(
        {
            f"{row['targetLabel']}/r{r}"
            for row in rows
            for r in row["tc0ScoreableSignatures"]
        }
    )
    payload = {
        "actionFamily": "cyclic-cubic rank-eight multiquadratic Kummer hyperplane",
        "exactChecks": {
            "allActionsOrder768": all(row["order"] == 768 for row in rows),
            "hyperplanes": len(rows),
            "labels": labels,
        },
        "frontierGeneratedAt": generated_at,
        "frontierPairs": len(frontier),
        "livePairs": live_pairs,
        "networkCalls": 0,
        "rows": rows,
        "submissionCalls": 0,
        "status": (
            "promote_to_arithmetic"
            if live_pairs
            else "blocked_zero_scoreable_tc0_support"
        ),
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
