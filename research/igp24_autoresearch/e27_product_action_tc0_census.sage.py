#!/usr/bin/env sage
"""Read-only census of direct Cartesian-product actions meeting current tc0.

This is deliberately target-first: it pins the current SQLite target rows,
constructs only the 481 product actions of degrees 2x12, 3x8, and 4x6, and
prints only actions having a current team-count-zero, undiscovered,
nonbaseline, locally-unaccepted signature.  It makes no network, submission,
ledger-write, outbox-write, or certificate-write calls.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from sage.all import PermutationGroup, PermutationGroupElement, TransitiveGroup, libgap


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
FAMILIES = (
    ("2x12", 2, 1, 12, 301),
    ("3x8", 3, 2, 8, 50),
    ("4x6", 4, 5, 6, 16),
)


def fixed_counts(group, degree: int) -> tuple[int, ...]:
    """Fixed-point counts of identity/involution class representatives."""
    counts = set()
    for representative in group.conjugacy_classes_representatives():
        if representative.order() not in (1, 2):
            continue
        counts.add(sum(representative(point) == point for point in range(1, degree + 1)))
    return tuple(sorted(counts))


def cartesian_product_action(left, left_degree: int, right, right_degree: int):
    """Natural action of left x right on {1..left_degree} x {1..right_degree}."""
    generators = []
    for permutation in left.gens():
        images = [
            (int(permutation(i)) - 1) * right_degree + j
            for i in range(1, left_degree + 1)
            for j in range(1, right_degree + 1)
        ]
        generators.append(PermutationGroupElement(images))
    for permutation in right.gens():
        images = [
            (i - 1) * right_degree + int(permutation(j))
            for i in range(1, left_degree + 1)
            for j in range(1, right_degree + 1)
        ]
        generators.append(PermutationGroupElement(images))
    return PermutationGroup(generators)


def target_snapshot():
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        target_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(targets)")
        }
        stamp_projection = (
            "MIN(generated_at),MAX(generated_at)"
            if "generated_at" in target_columns
            else "NULL,NULL"
        )
        total_rows, total_labels, minimum_stamp, maximum_stamp = connection.execute(
            "SELECT COUNT(*),COUNT(DISTINCT label)," + stamp_projection + " FROM targets"
        ).fetchone()
        undiscovered = " AND t.discovered=0" if "discovered" in target_columns else ""
        rows = connection.execute(
            "SELECT t.label,t.r FROM targets AS t "
            "LEFT JOIN baseline_pairs AS b ON b.label=t.label AND b.r=t.r "
            "WHERE t.team_count=0" + undiscovered + " AND b.label IS NULL "
            "AND NOT EXISTS ("
            " SELECT 1 FROM verifications AS v "
            " WHERE v.label=t.label AND v.r=t.r "
            " AND v.scoreable=1"
            ")"
        ).fetchall()
        connection.rollback()
    finally:
        connection.close()
    pairs = {(str(label), int(signature)) for label, signature in rows}
    by_label = {}
    for label, signature in pairs:
        by_label.setdefault(label, set()).add(signature)
    return {
        "boundary": {
            "databaseBytes": DB.stat().st_size,
            "databaseMtimeNs": DB.stat().st_mtime_ns,
            "walBytes": (
                (DB.parent / f"{DB.name}-wal").stat().st_size
                if (DB.parent / f"{DB.name}-wal").exists()
                else 0
            ),
            "targetRows": int(total_rows),
            "targetLabels": int(total_labels),
            "minimumGeneratedAt": minimum_stamp,
            "maximumGeneratedAt": maximum_stamp,
            "eligiblePairsBeforeReservationExclusions": len(pairs),
            "eligibleLabelsBeforeReservationExclusions": len(by_label),
        },
        "pairs": pairs,
        "byLabel": by_label,
    }


def main() -> int:
    snapshot = target_snapshot()
    print("E27_BOUNDARY " + json.dumps(snapshot["boundary"], sort_keys=True), flush=True)

    group_cache = {}
    signature_cache = {}

    def group(degree: int, index: int):
        key = (degree, index)
        if key not in group_cache:
            group_cache[key] = TransitiveGroup(degree, index)
        return group_cache[key]

    def signatures(degree: int, index: int):
        key = (degree, index)
        if key not in signature_cache:
            signature_cache[key] = fixed_counts(group(degree, index), degree)
        return signature_cache[key]

    action_count = 0
    identified = {}
    matching_routes = []
    for family, left_degree, left_count, right_degree, right_count in FAMILIES:
        for left_index in range(1, left_count + 1):
            left = group(left_degree, left_index)
            left_signatures = signatures(left_degree, left_index)
            for right_index in range(1, right_count + 1):
                right = group(right_degree, right_index)
                right_signatures = signatures(right_degree, right_index)
                product = cartesian_product_action(
                    left, left_degree, right, right_degree
                )
                if not product.is_transitive():
                    raise ArithmeticError("Cartesian product action is not transitive")
                target_index = int(libgap.TransitiveIdentification(libgap(product)))
                target_label = f"24T{target_index}"
                possible_signatures = tuple(
                    sorted(
                        {
                            left_signature * right_signature
                            for left_signature in left_signatures
                            for right_signature in right_signatures
                        }
                    )
                )
                action_count += 1
                key = (target_label, possible_signatures)
                identified.setdefault(key, []).append(
                    (family, left_degree, left_index, right_degree, right_index)
                )
                live_signatures = tuple(
                    sorted(
                        set(possible_signatures)
                        & snapshot["byLabel"].get(target_label, set())
                    )
                )
                if live_signatures:
                    decompositions = {
                        str(signature): [
                            [left_signature, right_signature]
                            for left_signature in left_signatures
                            for right_signature in right_signatures
                            if left_signature * right_signature == signature
                        ]
                        for signature in live_signatures
                    }
                    matching_routes.append(
                        {
                            "family": family,
                            "left": {
                                "label": f"{left_degree}T{left_index}",
                                "fixedCounts": left_signatures,
                            },
                            "right": {
                                "label": f"{right_degree}T{right_index}",
                                "fixedCounts": right_signatures,
                            },
                            "targetLabel": target_label,
                            "productOrder": int(product.order()),
                            "possibleR": possible_signatures,
                            "liveTc0R": live_signatures,
                            "factorSignatureDecompositions": decompositions,
                        }
                    )
                if action_count % 25 == 0:
                    print(
                        f"E27_PROGRESS actions={action_count} matches={len(matching_routes)}",
                        file=sys.stderr,
                        flush=True,
                    )

    matching_routes.sort(
        key=lambda row: (
            int(row["targetLabel"][3:]),
            row["family"],
            row["left"]["label"],
            row["right"]["label"],
        )
    )
    summary = {
        "actionPairsEnumerated": action_count,
        "distinctActionSignatureProfiles": len(identified),
        "distinctTargetLabels": len({key[0] for key in identified}),
        "matchingFactorPairs": len(matching_routes),
        "matchingTargetLabels": len(
            {row["targetLabel"] for row in matching_routes}
        ),
        "matchingPairsBeforeReservationExclusions": len(
            {
                (row["targetLabel"], signature)
                for row in matching_routes
                for signature in row["liveTc0R"]
            }
        ),
    }
    print("E27_SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    for row in matching_routes:
        print("E27_ROUTE " + json.dumps(row, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
