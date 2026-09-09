#!/usr/bin/env python3
"""Seal unsubmitted exact Kummer candidates that landed outside requested signatures."""

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


def canonical_hash(line: str) -> tuple[str, str]:
    canonical = ",".join(str(int(value)) for value in line.split(","))
    return canonical, hashlib.sha256(canonical.encode("ascii")).hexdigest()


def submitted_hashes(receipts: Path) -> set[str]:
    hashes: set[str] = set()
    for receipt_path in receipts.glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            if not manifest.is_absolute():
                manifest = ROOT / manifest
            body = manifest.read_bytes()
            if hashlib.sha256(body).hexdigest() != str(receipt["manifestHash"]):
                continue
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
        for raw in body.decode("utf-8").splitlines():
            value = raw.split("#", 1)[0].strip()
            if value:
                hashes.add(canonical_hash(value)[1])
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--max-team-count", type=int, default=15)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite spillover outputs")

    paths: list[Path] = []
    for pattern in args.input:
        matches = [Path(value) for value in sorted(glob.glob(str(ROOT / pattern)))]
        if not matches:
            raise FileNotFoundError(pattern)
        paths.extend(matches)

    receipts = submitted_hashes(args.receipts)
    skips: Counter[str] = Counter()
    best: dict[tuple[str, int], dict[str, object]] = {}
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for path in sorted(set(paths)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                skips["unreadable_artifact"] += 1
                continue
            for row in payload.get("candidates", []):
                required = {"coefficientLine", "coefficientSha256", "targetLabel", "r"}
                if not required.issubset(row):
                    skips["incomplete_candidate"] += 1
                    continue
                line, digest = canonical_hash(str(row["coefficientLine"]))
                if digest != str(row["coefficientSha256"]):
                    raise ValueError(f"candidate hash mismatch in {path}")
                if digest in receipts:
                    skips["receipt_hash"] += 1
                    continue
                if connection.execute(
                    "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
                ).fetchone():
                    skips["known_hash"] += 1
                    continue
                pair = (str(row["targetLabel"]), int(row["r"]))
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
                candidate = {
                    **row,
                    "coefficientLine": line,
                    "artifact": str(path.relative_to(ROOT)),
                    "targetTeamCount": team_count,
                    "targetMinimumDiscAbs": target["minimum_disc_abs"],
                    "targetGeneratedAt": str(target["generated_at"]),
                }
                rank = (
                    int(row.get("fieldDiscriminantAbs") or 10**999),
                    len(line),
                    digest,
                )
                incumbent = best.get(pair)
                if incumbent is None or rank < incumbent["rank"]:
                    if incumbent is not None:
                        skips["duplicate_pair"] += 1
                    candidate["rank"] = rank
                    best[pair] = candidate
                else:
                    skips["duplicate_pair"] += 1
    finally:
        connection.close()

    selected = sorted(
        best.values(),
        key=lambda row: (
            int(row["targetTeamCount"]),
            int(str(row["targetLabel"])[3:]),
            int(row["r"]),
        ),
    )
    if not selected:
        raise ValueError("no exact Kummer spillovers remain")
    manifest = "".join(str(row["coefficientLine"]) + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    projection = sum(
        (Fraction(1, 2 ** int(row["targetTeamCount"])) for row in selected),
        Fraction(0, 1),
    )
    summary = {
        "artifactCount": len(set(paths)),
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "maximumTeamCount": args.max_team_count,
        "projectedMarginalScoreExact": str(projection),
        "projectedMarginalScoreFloat": float(projection),
        "projectionQualifier": "contention only, before discriminant penalty",
        "selectedPairs": [
            {
                "artifact": row["artifact"],
                "coefficientSha256": row["coefficientSha256"],
                "fieldDiscriminantAbs": row.get("fieldDiscriminantAbs"),
                "label": row["targetLabel"],
                "r": row["r"],
                "teamCount": row["targetTeamCount"],
            }
            for row in selected
        ],
        "selectedRows": len(selected),
        "skipCounts": dict(sorted(skips.items())),
        "teamCountDistribution": dict(
            sorted(Counter(int(row["targetTeamCount"]) for row in selected).items())
        ),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
