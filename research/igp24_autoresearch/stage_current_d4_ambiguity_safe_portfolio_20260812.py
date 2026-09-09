#!/usr/bin/env python3
"""Stage a diversity-first D4 portfolio whose every profile match is live gold."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def digest_polynomial(line: str) -> str:
    return hashlib.sha256(line.strip().encode()).hexdigest()


def pending_hashes(connection: sqlite3.Connection) -> set[str]:
    hashes: set[str] = set()
    for (manifest_path,) in connection.execute(
        "SELECT manifest_path FROM submission_receipts"
    ):
        path = Path(str(manifest_path))
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                hashes.add(digest_polynomial(line))
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--max-ambiguity", type=int, default=5)
    parser.add_argument("--max-rows", type=int, default=60)
    parser.add_argument(
        "--pending-pair",
        action="append",
        default=[],
        help="LABEL/rR pair already queued; repeat for multiple pairs",
    )
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        submitted = pending_hashes(connection)
        submitted.update(
            str(row[0])
            for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        )
        live_gold = {
            (str(label), int(r))
            for label, r in connection.execute(
                """
                SELECT t.label,t.r
                FROM targets AS t
                LEFT JOIN baseline_pairs AS b
                  ON b.label=t.label AND b.r=t.r
                LEFT JOIN (
                    SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
                ) AS owned
                  ON owned.label=t.label AND owned.r=t.r
                WHERE t.team_count=0 AND b.label IS NULL AND owned.label IS NULL
                """
            )
        }
    finally:
        connection.close()

    candidates = []
    rejected = Counter()
    pending_pairs = set(args.pending_pair)
    seen_hashes: set[str] = set()
    with args.input.open(encoding="utf-8") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            row = json.loads(raw)
            digest = str(row["coefficientSha256"])
            if digest in seen_hashes:
                rejected["duplicate_input_hash"] += 1
                continue
            seen_hashes.add(digest)
            if digest in submitted:
                rejected["already_submitted"] += 1
                continue
            labels = sorted(
                {str(match["label"]) for match in row.get("compatibleGoldLabels", [])},
                key=lambda label: int(label[3:]),
            )
            if not labels or len(labels) > args.max_ambiguity:
                rejected["outside_ambiguity_limit"] += 1
                continue
            r = int(row["r"])
            pairs = tuple((label, r) for label in labels)
            if not all(pair in live_gold for pair in pairs):
                rejected["not_all_current_live_gold"] += 1
                continue
            coefficients = [int(value) for value in str(row["polynomial"]).split(",")]
            row["compatiblePairs"] = [f"{label}/r{r}" for label in labels]
            row["newCompatiblePairs"] = [
                pair for pair in row["compatiblePairs"] if pair not in pending_pairs
            ]
            if not row["newCompatiblePairs"]:
                rejected["all_outcomes_already_pending"] += 1
                continue
            row["ambiguityClass"] = "|".join(row["compatiblePairs"])
            row["selectionCost"] = [
                max(abs(value) for value in coefficients),
                sum(abs(value) for value in coefficients),
                len(str(row["polynomial"])),
                digest,
            ]
            candidates.append(row)

    # Keep the easiest representative per exact ambiguity class and sextic base.
    best_by_class_base: dict[tuple[str, str], dict] = {}
    for row in candidates:
        key = (str(row["ambiguityClass"]), str(row["sextic"]))
        current = best_by_class_base.get(key)
        if current is None or tuple(row["selectionCost"]) < tuple(current["selectionCost"]):
            best_by_class_base[key] = row

    pool = list(best_by_class_base.values())
    selected: list[dict] = []
    used_classes: set[str] = set()
    used_bases: Counter[str] = Counter()
    covered_pairs: Counter[str] = Counter()
    while pool and len(selected) < args.max_rows:
        def priority(row: dict) -> tuple:
            pair_novelty = sum(
                1 for pair in row["newCompatiblePairs"] if not covered_pairs[pair]
            )
            class_novelty = int(row["ambiguityClass"] not in used_classes)
            base_novelty = int(not used_bases[str(row["sextic"])])
            return (
                -class_novelty,
                -len(row["newCompatiblePairs"]) / len(row["compatiblePairs"]),
                -pair_novelty,
                -base_novelty,
                used_bases[str(row["sextic"])],
                len(row["compatiblePairs"]),
                tuple(row["selectionCost"]),
            )

        choice = min(pool, key=priority)
        pool.remove(choice)
        # One candidate per ambiguity class on the first portfolio pass.
        if choice["ambiguityClass"] in used_classes:
            continue
        selected.append(choice)
        used_classes.add(str(choice["ambiguityClass"]))
        used_bases[str(choice["sextic"])] += 1
        covered_pairs.update(choice["newCompatiblePairs"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = "".join(str(row["polynomial"]) + "\n" for row in selected)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(args.output)
    selection_path = args.selection or args.output.with_suffix(".selection.json")
    payload = {
        "campaign": "current-d4-ambiguity-safe-gold-portfolio",
        "input": str(args.input.resolve()),
        "liveGoldPairsCovered": sorted(covered_pairs),
        "maxAmbiguity": args.max_ambiguity,
        "pendingPairs": sorted(pending_pairs),
        "rejected": dict(sorted(rejected.items())),
        "rows": selected,
        "selectedAmbiguityClasses": len(used_classes),
        "selectedDistinctBases": len(used_bases),
        "selectedRows": len(selected),
    }
    temp_selection = selection_path.with_suffix(selection_path.suffix + ".tmp")
    temp_selection.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temp_selection.replace(selection_path)
    print(
        json.dumps(
            {
                "ambiguityClasses": len(used_classes),
                "distinctBases": len(used_bases),
                "liveGoldPairsCovered": len(covered_pairs),
                "output": str(args.output.resolve()),
                "rows": len(selected),
                "selection": str(selection_path.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
