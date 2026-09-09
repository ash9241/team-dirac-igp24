#!/usr/bin/env sage -python
"""Build the exact generic rational-twist action map for even degree-24 fields.

For a verified even polynomial P, negation pairs its roots in a G-invariant
two-point block system.  Twisting by a rational quadratic field ramified at a
new prime adjoins the global block flip z, so the generic target action is
<G,z>.  This local-only script enumerates every possible two-point block
system in each locally owned even source label and records the exact GAP
transitive label of <G,z>.  It makes no network or submission calls.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"
OUTPUT_PATH = ROOT / "data" / "even_twist_action_map.jsonl"


def is_even_degree_24(coefficients: str) -> bool:
    values = coefficients.split(",")
    return (
        len(values) == 25
        and values[-1] == "1"
        and all(int(values[index]) == 0 for index in range(1, 25, 2))
    )


def owned_even_labels(connection: sqlite3.Connection) -> list[tuple[str, int]]:
    found: dict[int, str] = {}
    query = """
        SELECT v.label,v.t,p.coefficients
        FROM verifications AS v
        JOIN polynomials AS p USING (submission_id,polynomial_index)
        WHERE v.scoreable=1 AND v.label IS NOT NULL AND v.t IS NOT NULL
        ORDER BY v.t
    """
    for label, t, coefficients in connection.execute(query):
        t = int(t)
        if t not in found and is_even_degree_24(str(coefficients)):
            found[t] = str(label)
    return [(found[t], t) for t in sorted(found)]


def block_system_key(blocks) -> tuple[tuple[int, int], ...]:
    return tuple(
        sorted(tuple(sorted(int(value) for value in block)) for block in blocks)
    )


def action_row(label: str, t: int) -> dict:
    group = libgap.TransitiveGroup(24, t)
    systems = {}
    # A two-point block system is equivalently a fixed-point-free involution
    # in the permutation centralizer: its 12 cycles are precisely the blocks.
    # This is substantially faster than enumerating all blocks for thousands
    # of source actions.
    centralizer = libgap.Centralizer(libgap.SymmetricGroup(24), group)
    for flip in libgap.Elements(centralizer):
        if int(libgap.Order(flip)) != 2 or int(libgap.NrMovedPoints(flip)) != 24:
            continue
        key = tuple(
            (point, int(libgap.OnPoints(point, flip)))
            for point in range(1, 25)
            if point < int(libgap.OnPoints(point, flip))
        )
        blocks = [libgap.Set(list(pair)) for pair in key]
        flip_in_source = bool(flip in group)
        if flip_in_source:
            target_t = t
            target_order = int(libgap.Size(group))
        else:
            target = libgap.Group(list(libgap.GeneratorsOfGroup(group)) + [flip])
            target_t = int(libgap.TransitiveIdentification(target))
            target_order = int(libgap.Size(target))
        block_action = libgap.Action(group, blocks, libgap.OnSets)
        systems[key] = {
            "blockActionOrder": int(libgap.Size(block_action)),
            "blockActionT12": int(libgap.TransitiveIdentification(block_action)),
            "blocks": [list(pair) for pair in key],
            "flipInSource": flip_in_source,
            "targetLabel": f"24T{target_t}",
            "targetOrder": target_order,
            "targetT": target_t,
        }
    if not systems:
        raise ValueError(f"{label} has no two-point block system")
    rows = list(systems.values())
    rows.sort(key=lambda row: (row["targetT"], row["blockActionT12"], row["blocks"]))
    return {
        "sourceLabel": label,
        "sourceOrder": int(libgap.Size(group)),
        "sourceT": t,
        "systemCount": len(rows),
        "systems": rows,
        "targetLabels": sorted({row["targetLabel"] for row in rows}),
    }


def write_atomic(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--checkpoint-every", type=int, default=100)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        labels = owned_even_labels(connection)
    finally:
        connection.close()
    if args.limit:
        labels = labels[: args.limit]

    existing = {}
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[int(row["sourceT"])] = row

    rows = []
    for index, (label, t) in enumerate(labels, start=1):
        rows.append(existing.get(t) or action_row(label, t))
        if args.checkpoint_every and index % args.checkpoint_every == 0:
            write_atomic(args.output, rows)
            print(json.dumps({"completed": index, "total": len(labels)}), flush=True)
    write_atomic(args.output, rows)

    print(
        json.dumps(
            {
                "ambiguousTargetLabels": sum(
                    len(row["targetLabels"]) > 1 for row in rows
                ),
                "mappedLabels": len(rows),
                "multipleBlockSystems": sum(row["systemCount"] > 1 for row in rows),
                "networkCalls": 0,
                "submissionCalls": 0,
                "uniqueGenericTargetLabels": len(
                    {target for row in rows for target in row["targetLabels"]}
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
