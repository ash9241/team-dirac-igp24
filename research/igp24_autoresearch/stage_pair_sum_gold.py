#!/usr/bin/env python3
"""Stage currently live, nonbaseline, locally unowned pair-sum candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "data" / "pair_sum_candidates.jsonl"
DEFAULT_OUTPUT = ROOT / "outbox" / "pair_sum_live_gold.txt"
DB_PATH = ROOT / "data" / "ledger.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--include-shared",
        action="store_true",
        help="include unowned pairs already held by other teams",
    )
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.input.read_text(encoding="utf-8").splitlines()
        if line
    ]
    best: dict[tuple[str, int], dict] = {}
    with sqlite3.connect(DB_PATH) as conn:
        for row in rows:
            if row.get("status") != "certified":
                continue
            label = str(row["targetLabel"])
            r = int(row["targetR"])
            target = conn.execute(
                "SELECT team_count FROM targets WHERE label=? AND r=?",
                (label, r),
            ).fetchone()
            if target is None:
                continue
            team_count = int(target[0])
            if team_count != 0 and not args.include_shared:
                continue
            if conn.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
                (label, r),
            ).fetchone():
                continue
            if conn.execute(
                "SELECT 1 FROM verifications "
                "WHERE label=? AND r=? AND scoreable=1 LIMIT 1",
                (label, r),
            ).fetchone():
                continue

            line = str(row["coefficientLine"])
            digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
            if digest != str(row["coefficientSha256"]):
                raise ValueError(f"candidate hash mismatch for {label}/r{r}")
            if conn.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
                (digest,),
            ).fetchone():
                raise ValueError(f"known coefficient hash selected for {label}/r{r}")

            key = (label, r)
            incumbent = best.get(key)
            if incumbent is None or int(row["polynomialDiscriminantAbs"]) < int(
                incumbent["polynomialDiscriminantAbs"]
            ):
                best[key] = {**row, "targetTeamCount": team_count}

    selected = sorted(
        best.values(),
        key=lambda row: (int(row["targetT"]), int(row["targetR"])),
    )
    lines = [str(row["coefficientLine"]) for row in selected]
    if len(lines) != len(set(lines)):
        raise ValueError("duplicate coefficient lines selected")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    temporary.replace(args.output)

    print(
        json.dumps(
            {
                "gold": sum(int(row["targetTeamCount"]) == 0 for row in selected),
                "includeShared": args.include_shared,
                "output": str(args.output),
                "projectedPoints": sum(
                    1.0 / (int(row["targetTeamCount"]) + 1)
                    for row in selected
                ),
                "selected": len(selected),
                "pairs": [
                    {
                        "label": row["targetLabel"],
                        "r": int(row["targetR"]),
                        "coefficientSha256": row["coefficientSha256"],
                        "targetTeamCount": int(row["targetTeamCount"]),
                    }
                    for row in selected
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
