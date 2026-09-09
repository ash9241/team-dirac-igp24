#!/usr/bin/env sage
"""Exact tc0 gate for multiquadratic degree-eight extensions of an S3 cubic.

The degree-24 action has three affine 8-point blocks.  Its translation module
is F_2^9 and the outer S3 permutes the blocks.  This gate identifies the full
module and every invariant hyperplane, computes exact involution fixed-point
counts, and intersects them with the frozen scoreable tc0 frontier.

No arithmetic, network, or submission action is performed.
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
OUTPUT = ROOT / "data" / "m29_s3_cubic_multiquadratic_gate_20260727.json"


def perm(images):
    return libgap.PermList(libgap(images))


def translation(vector):
    masks = [
        sum(int(vector[3 * block + bit]) << bit for bit in range(3))
        for block in range(3)
    ]
    return perm(
        [
            8 * block + (vertex ^ masks[block]) + 1
            for block in range(3)
            for vertex in range(8)
        ]
    )


def block_permutation(block_images):
    return perm(
        [
            8 * block_images[block] + vertex + 1
            for block in range(3)
            for vertex in range(8)
        ]
    )


def fixed_points(element):
    return sum(
        int(libgap.OnPoints(point, element)) == point for point in range(1, 25)
    )


def hyperplane_basis(encoded_form):
    field = GF(2)
    form = [(encoded_form >> bit) & 1 for bit in range(3)]
    vectors = []
    for vector in VectorSpace(field, 9):
        value = sum(
            field(form[bit]) * vector[3 * block + bit]
            for block in range(3)
            for bit in range(3)
        )
        if value == 0:
            vectors.append(vector)
    basis = Matrix(field, vectors).row_space().basis()
    if len(basis) != 8:
        raise ArithmeticError("relation did not define an invariant hyperplane")
    return form, basis


def scoreable_frontier(connection):
    return {
        (str(label), int(r))
        for label, r in connection.execute(
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


def identify(name, basis, frontier, relation=None):
    generators = [translation(vector) for vector in basis]
    generators.extend(
        [
            block_permutation([1, 2, 0]),
            block_permutation([1, 0, 2]),
        ]
    )
    group = libgap.Group(generators)
    if not bool(libgap.IsTransitive(group)):
        raise ArithmeticError(f"{name} is not transitive")
    target_label = f"24T{int(libgap.TransitiveIdentification(group))}"
    signatures = sorted(
        {
            fixed_points(libgap.Representative(conjugacy_class))
            for conjugacy_class in list(libgap.ConjugacyClasses(group))
            if int(libgap.Order(libgap.Representative(conjugacy_class))) in (1, 2)
        }
    )
    return {
        "kernelDimension": len(basis),
        "name": name,
        "order": int(libgap.Size(group)),
        "relation": relation,
        "signatures": signatures,
        "targetLabel": target_label,
        "tc0ScoreableSignatures": [
            r for r in signatures if (target_label, int(r)) in frontier
        ],
    }


def main():
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    frontier = scoreable_frontier(connection)
    ambient = VectorSpace(GF(2), 9)
    rows = [
        identify(
            "full-rank-nine-kummer-module",
            list(ambient.basis()),
            frontier,
        )
    ]
    for encoded_form in range(1, 8):
        form, basis = hyperplane_basis(encoded_form)
        rows.append(
            identify(
                f"invariant-hyperplane-{encoded_form}",
                basis,
                frontier,
                relation=form,
            )
        )
    live_pairs = sorted(
        {
            f"{row['targetLabel']}/r{r}"
            for row in rows
            for r in row["tc0ScoreableSignatures"]
        }
    )
    payload = {
        "actionFamily": "S3-cubic multiquadratic affine-cube blocks",
        "frontierGeneratedAt": connection.execute(
            "SELECT MAX(generated_at) FROM targets"
        ).fetchone()[0],
        "frontierPairs": len(frontier),
        "livePairs": live_pairs,
        "networkCalls": 0,
        "rows": rows,
        "status": (
            "promote_to_arithmetic"
            if live_pairs
            else "blocked_zero_scoreable_tc0_support"
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
