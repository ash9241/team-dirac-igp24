#!/usr/bin/env python3
"""Seal stranded exact labeled candidates from selected trusted artifacts."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sqlite3
from collections import Counter
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def walk(value):
    if isinstance(value, dict):
        if "coefficientLine" in value:
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--max-team-count", type=int, default=21)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite stranded-exact outputs")

    paths: list[Path] = []
    for pattern in args.input:
        paths.extend(Path(value) for value in sorted(glob.glob(str(ROOT / pattern))))
    if not paths:
        raise FileNotFoundError("no trusted artifacts matched")
    skips: Counter[str] = Counter()
    best: dict[tuple[str, int], tuple[tuple[int, int, str], dict]] = {}
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for path in sorted(set(paths)):
            try:
                objects = (
                    [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                    if path.suffix == ".jsonl"
                    else [json.loads(path.read_text())]
                )
            except (OSError, json.JSONDecodeError):
                skips["unreadable_artifact"] += 1
                continue
            for obj in objects:
                for row in walk(obj):
                    status = str(row.get("status", ""))
                    if path.name.startswith("agent_pair_sibling_") and status != "certified":
                        skips["uncertified_pair_sibling"] += 1
                        continue
                    label = row.get("targetLabel")
                    r = row.get("targetR") if row.get("targetR") is not None else row.get("r")
                    disc = row.get("fieldDiscriminantAbs")
                    if label is None or r is None or disc is None:
                        skips["incomplete_exact_schema"] += 1
                        continue
                    values = [int(value) for value in str(row["coefficientLine"]).split(",")]
                    if len(values) != 25 or values[-1] != 1:
                        skips["not_monic_degree_24"] += 1
                        continue
                    line = ",".join(str(value) for value in values)
                    digest = hashlib.sha256(line.encode()).hexdigest()
                    claimed = row.get("coefficientSha256")
                    if claimed is not None and digest != str(claimed):
                        raise ValueError(f"candidate hash mismatch in {path}")
                    if connection.execute(
                        "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
                    ).fetchone():
                        skips["known_hash"] += 1
                        continue
                    pair = (str(label), int(r))
                    if connection.execute(
                        "SELECT 1 FROM verifications WHERE label=? AND r=? AND status='accepted'",
                        pair,
                    ).fetchone():
                        skips["owned_pair"] += 1
                        continue
                    if connection.execute(
                        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
                    ).fetchone():
                        skips["baseline_pair"] += 1
                        continue
                    target = connection.execute(
                        "SELECT team_count FROM targets WHERE label=? AND r=?", pair
                    ).fetchone()
                    if target is None:
                        skips["missing_target"] += 1
                        continue
                    team_count = int(target["team_count"])
                    if team_count > args.max_team_count:
                        skips["above_team_count_cap"] += 1
                        continue
                    candidate = {
                        "artifact": str(path.relative_to(ROOT)),
                        "coefficientLine": line,
                        "coefficientSha256": digest,
                        "fieldDiscriminantAbs": str(disc),
                        "label": pair[0],
                        "r": pair[1],
                        "teamCount": team_count,
                    }
                    rank = (int(disc), len(line), digest)
                    if pair not in best or rank < best[pair][0]:
                        if pair in best:
                            skips["duplicate_pair"] += 1
                        best[pair] = (rank, candidate)
                    else:
                        skips["duplicate_pair"] += 1
    finally:
        connection.close()
    selected = sorted(
        (value[1] for value in best.values()),
        key=lambda row: (row["teamCount"], int(row["label"][3:]), row["r"]),
    )
    if not selected:
        raise ValueError("no stranded exact candidates remain")
    manifest = "".join(str(row["coefficientLine"]) + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest)
    projection = sum(
        (Fraction(1, 2 ** int(row["teamCount"])) for row in selected), Fraction(0, 1)
    )
    summary = {
        "inputArtifactCount": len(set(paths)),
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode()).hexdigest(),
        "projectedContentionValueExact": str(projection),
        "projectedContentionValueFloat": float(projection),
        "selectedPairs": [
            {key: row[key] for key in ("artifact", "coefficientSha256", "label", "r", "teamCount")}
            for row in selected
        ],
        "selectedRows": len(selected),
        "skipCounts": dict(sorted(skips.items())),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
