#!/usr/bin/env python3
"""Validate and value-dedupe exact C3 group-ring hybrid candidates."""

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


def canonical(line: str) -> str:
    values = [int(value) for value in line.split(",")]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not a monic degree-24 polynomial")
    return ",".join(map(str, values))


def receipt_hashes(path: Path) -> set[str]:
    output = set()
    for receipt_path in path.glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text())
            manifest = Path(receipt["manifest"])
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["manifestHash"]:
                continue
            for raw in manifest.read_text().splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    output.add(hashlib.sha256(canonical(line).encode()).hexdigest())
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--max-team-count", type=int, default=15)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite C3 value stage")
    paths = []
    for pattern in args.input:
        matches = [Path(value) for value in sorted(glob.glob(str(ROOT / pattern)))]
        if not matches:
            raise FileNotFoundError(pattern)
        paths.extend(matches)
    receipt = receipt_hashes(args.receipts)
    skips = Counter()
    eligible = []
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        for path in paths:
            artifact = json.loads(path.read_text())
            if artifact.get("schemaVersion") != "current-c3-group-ring-hybrid-v1":
                raise ValueError(f"unexpected schema in {path}")
            for row in artifact.get("results", []):
                if row.get("status") != "exact_hit":
                    continue
                line = canonical(str(row["candidateCoefficientLine"]))
                digest = hashlib.sha256(line.encode()).hexdigest()
                if digest != str(row["candidateSha256"]):
                    raise ValueError(f"candidate hash mismatch in {path}")
                exact_attempts = [
                    attempt for attempt in row.get("attempts", [])
                    if attempt.get("status") == "exact_hit"
                ]
                if len(exact_attempts) != 1 or not exact_attempts[0].get("certificate", {}).get("complete"):
                    raise ValueError(f"missing complete exact certificate in {path}")
                if canonical(str(exact_attempts[0]["candidateCoefficientLine"])) != line:
                    raise ValueError(f"attempt/selection mismatch in {path}")
                pair = str(row["targetLabel"]), int(row["targetR"])
                if connection.execute("SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)).fetchone():
                    skips["known_hash"] += 1
                    continue
                if digest in receipt:
                    skips["receipt_hash"] += 1
                    continue
                if connection.execute(
                    "SELECT 1 FROM verifications WHERE label=? AND r=? AND status='accepted'", pair
                ).fetchone():
                    skips["owned_pair"] += 1
                    continue
                if connection.execute("SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair).fetchone():
                    skips["baseline_pair"] += 1
                    continue
                target = connection.execute(
                    "SELECT team_count FROM targets WHERE label=? AND r=?", pair
                ).fetchone()
                if target is None or int(target[0]) > args.max_team_count:
                    skips["ineligible_pair"] += 1
                    continue
                eligible.append({
                    "artifact": str(path.relative_to(ROOT)),
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "label": pair[0],
                    "r": pair[1],
                    "teamCount": int(target[0]),
                })
    finally:
        connection.close()
    selected = {}
    for row in eligible:
        pair = row["label"], row["r"]
        incumbent = selected.get(pair)
        if incumbent is None or (len(row["coefficientLine"]), row["coefficientSha256"]) < (
            len(incumbent["coefficientLine"]), incumbent["coefficientSha256"]
        ):
            if incumbent is not None:
                skips["duplicate_pair"] += 1
            selected[pair] = row
        else:
            skips["duplicate_pair"] += 1
    rows = sorted(selected.values(), key=lambda row: (row["teamCount"], int(row["label"][3:]), row["r"]))
    if not rows:
        raise ValueError("no C3 value candidates remain")
    manifest = "".join(row["coefficientLine"] + "\n" for row in rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest)
    projection = sum((Fraction(1, 2 ** row["teamCount"]) for row in rows), Fraction())
    summary = {
        "checks": {
            "allCandidateHashesVerified": True,
            "allExactCertificatesComplete": True,
            "ledgerAndReceiptsExcluded": True,
        },
        "inputs": [str(path.relative_to(ROOT)) for path in paths],
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode()).hexdigest(),
        "projectedMarginalScoreExact": str(projection),
        "projectedMarginalScoreFloat": float(projection),
        "selectedPairs": rows,
        "selectedRows": len(rows),
        "skipCounts": dict(sorted(skips.items())),
        "teamCountDistribution": dict(sorted(Counter(row["teamCount"] for row in rows).items())),
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
