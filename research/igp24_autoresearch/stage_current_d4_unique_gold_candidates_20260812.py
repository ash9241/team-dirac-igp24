#!/usr/bin/env python3
"""Stage one new live candidate for each profile-unique D4 gold pair."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--exclude-pair",
        action="append",
        default=[],
        help="predicted LABEL/rR pair already pending in another manifest",
    )
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.input.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        eligible = []
        for row in rows:
            matches = row.get("compatibleGoldLabels", [])
            if len(matches) != 1:
                continue
            pair = (str(matches[0]["label"]), int(row["r"]))
            target = connection.execute(
                "SELECT team_count FROM targets WHERE label=? AND r=?", pair
            ).fetchone()
            baseline = connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
            ).fetchone()
            owned = connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1", pair
            ).fetchone()
            known = connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
                (str(row["coefficientSha256"]),),
            ).fetchone()
            pair_text = f"{pair[0]}/r{pair[1]}"
            if (
                target is not None
                and int(target[0]) == 0
                and baseline is None
                and owned is None
                and known is None
                and pair_text not in set(args.exclude_pair)
            ):
                eligible.append((pair, row))
    finally:
        connection.close()

    best = {}
    for pair, row in eligible:
        score = (
            len(str(row["polynomial"])),
            max(abs(int(value)) for value in str(row["polynomial"]).split(",")),
            str(row["coefficientSha256"]),
        )
        if pair not in best or score < best[pair][0]:
            best[pair] = (score, row)

    selected = [best[pair][1] for pair in sorted(best)]
    rendered = "".join(str(row["polynomial"]) + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "pairs": [
                    f"{row['compatibleGoldLabels'][0]['label']}/r{row['r']}"
                    for row in selected
                ],
                "rows": len(selected),
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
