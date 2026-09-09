#!/usr/bin/env python3
"""Select one owned presentation for every label with a 6T11 quotient."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inventory",
        type=Path,
        default=ROOT / "data" / "agent_non12_tower_owned_inventory.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "current_6t11_subfield_recovery_tasks_20260812.jsonl",
    )
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))["labels"]
    tasks = []
    for row in inventory:
        if not any(
            system.get("shape") == "6x4"
            and system.get("quotientActionLabel") == "6T11"
            for system in row.get("blockSystems", [])
        ):
            continue
        source = row["bestPresentation"]
        tasks.append(
            {
                "degrees": [6],
                "polynomialIndex": int(source["polynomialIndex"]),
                "sourceLabel": str(row["label"]),
                "submissionId": str(source["submissionId"]),
            }
        )
    tasks.sort(key=lambda row: int(row["sourceLabel"][3:]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in tasks),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output.resolve()), "tasks": len(tasks)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
