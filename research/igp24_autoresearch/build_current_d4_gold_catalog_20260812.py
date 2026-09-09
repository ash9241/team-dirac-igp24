#!/usr/bin/env python3
"""Build the live 6T11/D4 gold target/profile catalog for the emergency run."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db", type=Path, default=ROOT / "data" / "ledger.sqlite3"
    )
    parser.add_argument(
        "--structures",
        type=Path,
        default=ROOT / "data" / "agent_non12_tower_structures.jsonl",
    )
    parser.add_argument(
        "--profiles",
        type=Path,
        default=ROOT
        / "data"
        / "p27_6t11_signcode_compatible_catalog_20260727.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "current_6t11_d4_gold_catalog_20260812.json",
    )
    args = parser.parse_args()

    structural = {}
    with args.structures.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            systems = [
                system
                for system in row.get("blockSystems", [])
                if system.get("shape") == "6x4"
                and system.get("fiberActionLabel") == "4T3"
                and system.get("quotientActionLabel") == "6T11"
            ]
            if systems:
                structural[str(row["label"])] = {
                    "kernelOrders": sorted(
                        {int(system["blockKernelOrder"]) for system in systems}
                    ),
                    "systemCount": len(systems),
                    "t": int(row["t"]),
                }

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT t.label,t.r,t.minimum_disc_abs,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.team_count=0 AND b.label IS NULL AND owned.label IS NULL
            ORDER BY t.t,t.r
            """
        ).fetchall()
    finally:
        connection.close()

    pairs_by_label: dict[str, list[dict]] = defaultdict(list)
    for label, target_r, minimum, generated in rows:
        label = str(label)
        if label not in structural:
            continue
        pairs_by_label[label].append(
            {
                "generatedAt": str(generated) if generated else None,
                "minimumDiscAbs": str(minimum) if minimum else None,
                "r": int(target_r),
                "teamCount": 0,
            }
        )

    profiles = json.loads(args.profiles.read_text(encoding="utf-8"))
    profile_by_label = {str(row["label"]): row for row in profiles["groups"]}
    missing = sorted(set(pairs_by_label) - set(profile_by_label))

    output_rows = []
    for label in sorted(
        set(pairs_by_label) & set(profile_by_label),
        key=lambda value: int(value[3:]),
    ):
        profile = profile_by_label[label]
        output_rows.append(
            {
                "cycleProfiles": profile["cycleProfiles"],
                "goldPairs": pairs_by_label[label],
                "kernelOrders": structural[label]["kernelOrders"],
                "label": label,
                "order": int(profile["order"]),
                "possibleRealRoots": [int(value) for value in profile["possibleRealRoots"]],
                "systemCount": structural[label]["systemCount"],
                "t": structural[label]["t"],
            }
        )

    payload = {
        "campaign": "current-6T11-D4-gold",
        "goldLabelCount": len(output_rows),
        "goldPairCount": sum(len(row["goldPairs"]) for row in output_rows),
        "missingProfileLabels": missing,
        "profiles": str(args.profiles.resolve()),
        "profilesSha256": sha256(args.profiles),
        "quotientAction": "6T11",
        "fiberAction": "4T3",
        "rows": output_rows,
        "structures": str(args.structures.resolve()),
        "structuresSha256": sha256(args.structures),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "goldLabels": payload["goldLabelCount"],
                "goldPairs": payload["goldPairCount"],
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
