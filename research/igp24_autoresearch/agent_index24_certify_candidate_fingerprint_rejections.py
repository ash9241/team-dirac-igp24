#!/usr/bin/env python3
"""Emit exact nonisomorphism rows for candidate fingerprint mismatches."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path


FIELDS = (
    "abelianInvariants",
    "centerAbelianInvariants",
    "centerOrder",
    "conjugacyClassCount",
    "conjugacyProfileSha256",
    "derivedAbelianInvariants",
    "derivedCenterOrder",
    "derivedOrder",
    "isAbelian",
    "isNilpotent",
    "isPerfect",
    "isSolvable",
    "order",
    "secondDerivedOrder",
)


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--fingerprints", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fingerprints = {}
    for pattern in args.fingerprints:
        for filename in glob.glob(pattern):
            for row in read_jsonl(Path(filename)):
                fingerprints[int(row["t"])] = row
    rows = []
    for candidate in read_jsonl(args.candidates):
        source = fingerprints[int(candidate["sourceT"])]
        target = fingerprints[int(candidate["targetT"])]
        if source["fingerprintSha256"] == target["fingerprintSha256"]:
            continue
        differences = {
            field: {"source": source.get(field), "target": target.get(field)}
            for field in FIELDS
            if source.get(field) != target.get(field)
        }
        if not differences:
            raise ArithmeticError("fingerprint hashes differ without an invariant difference")
        rows.append({
            **candidate,
            "actionKind": "core_free_index_24_subgroup",
            "nonisomorphismCertificate": {
                "differences": differences,
                "sourceFingerprintSha256": str(source["fingerprintSha256"]),
                "targetFingerprintSha256": str(target["fingerprintSha256"]),
                "type": "exact_isomorphism_invariant_mismatch",
            },
            "status": "certified_nonisomorphic",
        })
    write_jsonl(args.output, rows)
    print(json.dumps({"certifiedNonisomorphic": len(rows), "output": str(args.output.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
