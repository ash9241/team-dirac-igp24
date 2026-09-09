#!/usr/bin/env python3
"""Stage exact lower-Kummer result artifacts against the current live ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def parse_pair(value: str) -> tuple[str, int]:
    label, r_text = value.split("/r", 1)
    return label, int(r_text)


def receipt_hashes(receipts: Path) -> set[str]:
    hashes: set[str] = set()
    for receipt_path in receipts.glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            recorded = str(receipt["manifestHash"])
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
        if not manifest.is_file() or hashlib.sha256(manifest.read_bytes()).hexdigest() != recorded:
            continue
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            canonical = ",".join(str(int(value)) for value in line.split(","))
            hashes.add(hashlib.sha256(canonical.encode("ascii")).hexdigest())
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--max-team-count", type=int, default=3)
    parser.add_argument("--exclude-pair", action="append", default=[])
    parser.add_argument("--exclude-summary", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite staged lower-Kummer output")

    paths: list[Path] = []
    for pattern in args.input:
        matches = sorted(ROOT.glob(pattern))
        if not matches:
            raise FileNotFoundError(pattern)
        paths.extend(matches)
    excluded_pairs = {parse_pair(value) for value in args.exclude_pair}
    for summary_path in args.exclude_summary:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        excluded_pairs.update(
            (str(row["label"]), int(row["r"]))
            for row in summary.get("selectedPairs", [])
        )
    pending_hashes = receipt_hashes(args.receipts)
    skips = Counter()
    candidates = []

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for row in payload.get("candidates", []):
                if not {
                    "coefficientLine",
                    "coefficientSha256",
                    "fieldDiscriminantAbs",
                    "targetLabel",
                    "r",
                }.issubset(row):
                    skips["non_exact_candidate_schema"] += 1
                    continue
                line = str(row["coefficientLine"])
                digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                if digest != str(row["coefficientSha256"]):
                    raise ValueError(f"coefficient hash mismatch in {path}")
                pair = (str(row["targetLabel"]), int(row["r"]))
                if pair in excluded_pairs:
                    skips["explicitly_excluded_pair"] += 1
                    continue
                if digest in pending_hashes:
                    skips["receipt_hash"] += 1
                    continue
                if connection.execute(
                    "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
                ).fetchone():
                    skips["known_hash"] += 1
                    continue
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
                    "SELECT team_count,minimum_disc_abs,generated_at FROM targets "
                    "WHERE label=? AND r=?",
                    pair,
                ).fetchone()
                if target is None:
                    skips["missing_target"] += 1
                    continue
                team_count = int(target["team_count"])
                if team_count > args.max_team_count:
                    skips["above_team_count_cap"] += 1
                    continue
                candidates.append(
                    {
                        **row,
                        "artifact": str(path.relative_to(ROOT)),
                        "targetTeamCount": team_count,
                        "targetMinimumDiscAbs": target["minimum_disc_abs"],
                        "targetGeneratedAt": str(target["generated_at"]),
                    }
                )
    finally:
        connection.close()

    best: dict[tuple[str, int], dict] = {}
    for row in candidates:
        pair = (str(row["targetLabel"]), int(row["r"]))
        rank = (
            int(row["fieldDiscriminantAbs"]),
            len(str(row["coefficientLine"])),
            str(row["coefficientSha256"]),
        )
        incumbent = best.get(pair)
        if incumbent is None:
            best[pair] = row
        else:
            skips["duplicate_pair"] += 1
            incumbent_rank = (
                int(incumbent["fieldDiscriminantAbs"]),
                len(str(incumbent["coefficientLine"])),
                str(incumbent["coefficientSha256"]),
            )
            if rank < incumbent_rank:
                best[pair] = row

    selected = sorted(
        best.values(),
        key=lambda row: (
            int(row["targetTeamCount"]),
            int(str(row["targetLabel"])[3:]),
            int(row["r"]),
        ),
    )
    if not selected:
        raise ValueError("no current exact lower-Kummer value candidates remain")
    projection = sum(
        (Fraction(1, 2 ** int(row["targetTeamCount"])) for row in selected),
        Fraction(0, 1),
    )
    manifest = "".join(str(row["coefficientLine"]) + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    summary = {
        "inputs": [str(path.relative_to(ROOT)) for path in paths],
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "projectedMarginalScoreExact": str(projection),
        "projectionQualifier": "before discriminant penalty",
        "selectedPairs": [
            {
                "artifact": row["artifact"],
                "coefficientSha256": row["coefficientSha256"],
                "fieldDiscriminantAbs": row["fieldDiscriminantAbs"],
                "label": row["targetLabel"],
                "r": row["r"],
                "teamCount": row["targetTeamCount"],
                "targetGeneratedAt": row["targetGeneratedAt"],
            }
            for row in selected
        ],
        "selectedRows": len(selected),
        "skipCounts": dict(sorted(skips.items())),
        "teamCountDistribution": dict(
            sorted(Counter(int(row["targetTeamCount"]) for row in selected).items())
        ),
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
