#!/usr/bin/env sage -python
"""Join universal quadratic sign-code wreath layers to current tc0 signatures."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
DB = DATA / "ledger.sqlite3"
OUTPUT = DATA / "quadratic_signcode_gold_census.json"


def block_lift(permutation) -> object:
    images = []
    for block in range(1, 13):
        target = int(libgap.OnPoints(block, permutation))
        images.extend([2 * target - 1, 2 * target])
    return libgap.PermList(images)


def flip_blocks(indexes: set[int]) -> object:
    images = list(range(1, 25))
    for index in indexes:
        first = 2 * index + 1
        second = first + 1
        images[first - 1], images[second - 1] = second, first
    return libgap.PermList(images)


def signcode_group(quotient_t: int, layer: str) -> tuple[str, int]:
    quotient = libgap.TransitiveGroup(12, quotient_t)
    generators = [
        block_lift(generator)
        for generator in libgap.GeneratorsOfGroup(quotient)
    ]
    if layer == "diagonal":
        generators.append(flip_blocks(set(range(12))))
        dimension = 1
    elif layer == "even":
        generators.extend(
            flip_blocks({index, 11}) for index in range(11)
        )
        dimension = 11
    elif layer == "full":
        generators.extend(flip_blocks({index}) for index in range(12))
        dimension = 12
    else:
        raise ValueError("unknown sign-code layer")
    group = libgap.Group(generators)
    expected = (2**dimension) * int(libgap.Size(quotient))
    if int(libgap.Size(group)) != expected or not bool(libgap.IsTransitive(group)):
        raise ArithmeticError("quadratic sign-code semidirect product mismatch")
    target_t = int(libgap.TransitiveIdentification(group))
    return f"24T{target_t}", expected


def main() -> int:
    if OUTPUT.exists():
        raise ValueError(f"refusing to overwrite {OUTPUT}")
    quotient_ts = set()
    for line in ACTION_MAP.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        values = {
            int(system["blockActionT12"])
            for system in row.get("systems") or []
            if bool(system.get("flipInSource"))
        }
        if len(values) == 1:
            quotient_ts.update(values)

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    tc0 = {}
    for label, signature in connection.execute(
        """
        SELECT t.label,t.r FROM targets t
        WHERE t.team_count=0 AND t.discovered=0
          AND NOT EXISTS(
            SELECT 1 FROM baseline_pairs b
            WHERE b.label=t.label AND b.r=t.r
          )
          AND NOT EXISTS(
            SELECT 1 FROM verifications v
            WHERE v.label=t.label AND v.r=t.r AND v.status='accepted'
          )
        """
    ):
        tc0.setdefault(str(label), []).append(int(signature))
    generated_at = connection.execute(
        "SELECT MAX(generated_at) FROM targets"
    ).fetchone()[0]
    connection.close()

    rows = []
    for quotient_t in sorted(quotient_ts):
        for layer in ("diagonal", "even", "full"):
            label, order = signcode_group(quotient_t, layer)
            signatures = sorted(tc0.get(label, []))
            if signatures:
                rows.append(
                    {
                        "kernelDimension": {
                            "diagonal": 1,
                            "even": 11,
                            "full": 12,
                        }[layer],
                        "layer": layer,
                        "liveTc0Signatures": signatures,
                        "quotientT12": quotient_t,
                        "targetLabel": label,
                        "targetOrder": order,
                    }
                )
    rows.sort(
        key=lambda row: (
            -len(row["liveTc0Signatures"]),
            row["kernelDimension"],
            int(row["targetLabel"][3:]),
        )
    )
    result = {
        "schemaVersion": "quadratic-signcode-gold-census-v1",
        "status": "exact_group_census_complete",
        "targetsGeneratedAt": generated_at,
        "quotientActions": len(quotient_ts),
        "rows": rows,
        "tc0Pairs": sum(len(row["liveTc0Signatures"]) for row in rows),
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0},
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "output": str(OUTPUT.relative_to(ROOT)),
                "rows": len(rows),
                "tc0Pairs": result["tc0Pairs"],
                "top": rows[:12],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
