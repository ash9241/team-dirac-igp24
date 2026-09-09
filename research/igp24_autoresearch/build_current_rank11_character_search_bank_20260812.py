#!/usr/bin/env python3
"""Build a current live no-aux character search bank from exact census fields."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--census",
        type=Path,
        default=ROOT / "data" / "current_rank11_character_field_census_20260812.jsonl",
    )
    parser.add_argument(
        "--structures",
        type=Path,
        default=ROOT / "data" / "agent_non12_tower_structures.jsonl",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "data" / "current_rank11_character_ledger_20260812.sqlite3",
    )
    parser.add_argument(
        "--bank",
        type=Path,
        default=ROOT / "data" / "current_rank11_character_search_bank_20260812.jsonl",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=ROOT / "data" / "current_rank11_character_search_bank_20260812_summary.json",
    )
    parser.add_argument("--fields-per-pair", type=int, default=2)
    parser.add_argument("--max-candidates", type=int, default=128)
    parser.add_argument("--witness-primes", type=int, default=1000)
    args = parser.parse_args()

    quotient_by_label = {}
    with args.structures.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            systems = [
                system
                for system in row.get("blockSystems", [])
                if system.get("shape") == "12x2"
                and int(system.get("blockKernelOrder", 0)) == 2**11
            ]
            if systems:
                quotient_by_label[str(row["label"])] = int(
                    str(systems[0]["quotientActionLabel"])[3:]
                )

    fields = [
        json.loads(line)
        for line in args.census.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # New exact fields first, then smaller field discriminant and source size.
    options_by_label: dict[str, list[dict]] = defaultdict(list)
    for field in fields:
        for label, cores in field.get("targetNormCores", {}).items():
            for representative in field.get("representatives", []):
                source = representative["source"]
                options_by_label[str(label)].append(
                    {
                        "cores": sorted({int(value) for value in cores}),
                        "fieldCanonicalSha256": str(field["fieldCanonicalSha256"]),
                        "fieldDiscAbs": int(field["fieldDiscAbs"]),
                        "newField": not bool(field.get("reusedExactAlignment", False)),
                        "source": source,
                    }
                )
    for label, options in options_by_label.items():
        unique = {}
        for option in options:
            key = (
                option["fieldCanonicalSha256"],
                str(option["source"]["submissionId"]),
                int(option["source"]["polynomialIndex"]),
            )
            unique.setdefault(key, option)
        options_by_label[label] = sorted(
            unique.values(),
            key=lambda option: (
                not option["newField"],
                option["fieldDiscAbs"],
                len(str(option["source"].get("fieldDiscAbs", ""))),
                str(option["source"]["coefficientSha256"]),
            ),
        )

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        live = connection.execute(
            """
            SELECT t.label,t.r,t.generated_at,t.minimum_disc_abs
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

    rows = []
    command_index = 0
    fields_used = set()
    for label, target_r, generated, minimum in live:
        label = str(label)
        if label not in quotient_by_label or label not in options_by_label:
            continue
        chosen = []
        used_fields = set()
        for option in options_by_label[label]:
            field_key = option["fieldCanonicalSha256"]
            if field_key in used_fields:
                continue
            chosen.append(option)
            used_fields.add(field_key)
            if len(chosen) >= args.fields_per_pair:
                break
        attempts = []
        for option in chosen:
            source = option["source"]
            for core in option["cores"][:1]:
                identity = (
                    label,
                    int(target_r),
                    str(source["submissionId"]),
                    int(source["polynomialIndex"]),
                    int(core),
                )
                digest = hashlib.sha256("|".join(map(str, identity)).encode()).hexdigest()
                output = (
                    ROOT
                    / "data"
                    / "current_rank11_character_search_outputs_20260812"
                    / f"{label}_r{target_r}__{digest[:14]}.json"
                )
                command = [
                    "/usr/bin/sage",
                    "-python",
                    "character_kernel_gold_pilot.sage.py",
                    "--db",
                    str(args.db.resolve()),
                    "--submission-id",
                    str(source["submissionId"]),
                    "--polynomial-index",
                    str(source["polynomialIndex"]),
                    "--target-label",
                    label,
                    "--target-r",
                    str(int(target_r)),
                    "--norm-core",
                    str(int(core)),
                    "--max-candidates",
                    str(args.max_candidates),
                    "--witness-primes",
                    str(args.witness_primes),
                    "--seed",
                    str(1 + int(digest[:8], 16) % 2_000_000_000),
                    "--output",
                    str(output.relative_to(ROOT)),
                ]
                attempts.append(
                    {
                        "command": command,
                        "fieldCanonicalSha256": option["fieldCanonicalSha256"],
                        "globalCommandIndex": command_index,
                        "identity": list(identity),
                        "newExactField": option["newField"],
                        "output": str(output.resolve()),
                        "targetNormCore": int(core),
                    }
                )
                command_index += 1
                fields_used.add(option["fieldCanonicalSha256"])
        if attempts:
            rows.append(
                {
                    "attempts": attempts,
                    "logicalTaskId": f"{label}_r{int(target_r)}",
                    "status": "executable_current_rank11_no_aux",
                    "target": {
                        "generatedAt": str(generated) if generated else None,
                        "label": label,
                        "minimumDiscAbs": str(minimum) if minimum else None,
                        "quotientT12": quotient_by_label[label],
                        "r": int(target_r),
                        "teamCount": 0,
                    },
                }
            )

    rendered = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    args.bank.parent.mkdir(parents=True, exist_ok=True)
    args.bank.write_text(rendered, encoding="utf-8")
    summary = {
        "bank": str(args.bank.resolve()),
        "bankSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "commandCount": command_index,
        "distinctExactFields": len(fields_used),
        "fieldsPerPair": args.fields_per_pair,
        "livePairCount": len(rows),
        "maxCandidates": args.max_candidates,
        "networkCalls": 0,
        "submissionCalls": 0,
        "witnessPrimes": args.witness_primes,
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
