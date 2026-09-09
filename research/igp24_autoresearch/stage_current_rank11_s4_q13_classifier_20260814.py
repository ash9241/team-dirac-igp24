#!/usr/bin/env python3
"""Stage a bounded, deduplicated S4-over-6T13 classifier portfolio.

Only prescribed-resolvent rows that remain Frobenius-compatible with at least
one current Alex-only target pair are eligible.  The natural-resolvent family
is excluded because a deep Frobenius audit places its first realization in a
proper index-16 subgroup.  This script performs no network call or submission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
PLACEMENTS = ROOT / "data" / "current_rank11_alex_20260812_unique_placements.jsonl"
DEFAULT_MANIFEST = ROOT / "outbox" / "current_rank11_s4_q13_classifier_20260814.txt"
DEFAULT_AUDIT = ROOT / "data" / "current_rank11_s4_q13_classifier_20260814_audit.json"


def rows(path: Path):
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            yield line_number, json.loads(line)


def coefficient_hash(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def validate_line(line: str) -> None:
    values = [int(value) for value in line.split(",")]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not a monic degree-24 coefficient line")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="+", type=Path)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--placements", type=Path, default=PLACEMENTS)
    parser.add_argument(
        "--recovery-targets",
        type=Path,
        help="use a live JSON pair map and point weights instead of Alex-only placements",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--max-rows", type=int, default=1000)
    parser.add_argument("--max-bytes", type=int, default=950000)
    parser.add_argument(
        "--allow-families",
        default="prescribed",
        help="comma-separated construction families eligible for this packet",
    )
    args = parser.parse_args()
    if args.manifest.exists() or args.audit.exists():
        raise FileExistsError("refusing to overwrite staged classifier artifacts")

    if args.recovery_targets:
        recovery_payload = json.loads(args.recovery_targets.read_text(encoding="utf-8"))
        target_pairs = {
            (str(row["label"]), int(row["r"])): {
                "teamCount": int(row["teamCount"]),
                "pointValue": 1.0 / (int(row["teamCount"]) + 1),
            }
            for row in recovery_payload.values()
        }
    else:
        target_pairs = {
            (str(row["label"]), int(row["r"])): {
                "teamCount": 1,
                "pointValue": 0.5,
            }
            for _, row in rows(args.placements)
        }
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    known_hashes = {
        str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    locally_covered = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            """
            SELECT DISTINCT label,r FROM verifications
            WHERE label IS NOT NULL AND r IS NOT NULL
              AND status='accepted' AND (scoreable=1 OR scoring_status='pending')
            """
        )
    }
    connection.close()

    allowed_families = {
        value.strip() for value in args.allow_families.split(",") if value.strip()
    }
    if not allowed_families:
        raise ValueError("--allow-families must name at least one family")
    candidates = []
    seen = set()
    rejections = Counter()
    for path in args.input:
        for line_number, row in rows(path):
            if str(row.get("constructionFamily")) not in allowed_families:
                rejections["constructionFamilyExcluded"] += 1
                continue
            line = str(row["polynomial"])
            validate_line(line)
            digest = coefficient_hash(line)
            if digest != str(row["coefficientSha256"]):
                raise ValueError(f"hash mismatch in {path}:{line_number}")
            if digest in known_hashes:
                rejections["ledgerHash"] += 1
                continue
            if digest in seen:
                rejections["waveDuplicateHash"] += 1
                continue
            r = int(row["r"])
            compatible_pair_keys = sorted(
                {
                    (str(target["label"]), r)
                    for target in row["compatibleTargets"]
                    if (str(target["label"]), r) in target_pairs
                    and (str(target["label"]), r) not in locally_covered
                }
            )
            if not compatible_pair_keys:
                rejections["noOpenTargetCompatiblePair"] += 1
                continue
            compatible_pairs = [
                {
                    "label": label,
                    "r": pair_r,
                    **target_pairs[(label, pair_r)],
                }
                for label, pair_r in compatible_pair_keys
            ]
            seen.add(digest)
            candidates.append(
                {
                    "source": str(path),
                    "sourceLine": line_number,
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "coefficientBytes": len((line + "\n").encode("ascii")),
                    "r": r,
                    "compatiblePairs": compatible_pairs,
                    "compatiblePairCount": len(compatible_pairs),
                    "compatiblePointCeiling": sum(
                        pair["pointValue"] for pair in compatible_pairs
                    ),
                    "discriminantTwist": int(row["discriminantTwist"]),
                    "s": str(row["s"]),
                    "tParameter": str(row["tParameter"]),
                    "baseIndex": int(row["baseIndex"]),
                }
            )

    # Favor broad target ambiguity (more ways to score), signature diversity,
    # and compact representatives.  Selection remains deterministic.
    candidates.sort(
        key=lambda row: (
            -row["compatiblePointCeiling"],
            -row["compatiblePairCount"],
            row["coefficientBytes"],
            row["r"],
            row["coefficientSha256"],
        )
    )
    selected = []
    selected_bytes = 0
    for row in candidates:
        if len(selected) >= args.max_rows:
            rejections["rowCap"] += 1
            continue
        if selected_bytes + row["coefficientBytes"] > args.max_bytes:
            rejections["byteCap"] += 1
            continue
        selected.append(row)
        selected_bytes += row["coefficientBytes"]

    manifest = "".join(row["coefficientLine"] + "\n" for row in selected)
    manifest_hash = hashlib.sha256(manifest.encode("ascii")).hexdigest()
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(manifest, encoding="ascii")
    potential_pairs = {
        (pair["label"], int(pair["r"])): float(pair["pointValue"])
        for row in selected
        for pair in row["compatiblePairs"]
    }
    audit = {
        "schemaVersion": "rank11-s4-q13-classifier-stage-v1",
        "allowedConstructionFamilies": sorted(allowed_families),
        "inputs": [str(path) for path in args.input],
        "eligibleRows": len(candidates),
        "selectedRows": len(selected),
        "selectedBytes": selected_bytes,
        "manifest": str(args.manifest),
        "manifestSha256": manifest_hash,
        "potentialTargetPairs": len(potential_pairs),
        "potentialPointCeiling": sum(potential_pairs.values()),
        "candidateCountPointCeiling": sum(
            max(pair["pointValue"] for pair in row["compatiblePairs"])
            for row in selected
        ),
        "potentialAlexOnlyPairs": len(potential_pairs) if not args.recovery_targets else None,
        "potentialAlexOnlyPointCeiling": (
            0.5 * len(potential_pairs) if not args.recovery_targets else None
        ),
        "signatureDistribution": dict(sorted(Counter(row["r"] for row in selected).items())),
        "twistDistribution": dict(
            sorted(Counter(row["discriminantTwist"] for row in selected).items())
        ),
        "rejections": dict(sorted(rejections.items())),
        "selected": selected,
        "safety": {
            "onlyExplicitConstructionFamilies": True,
            "allHashesAbsentFromLedger": True,
            "allRowsMonicDegree24": True,
            "allRowsProfileCompatibleWithOpenTargetPair": True,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
    }
    args.audit.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "manifest": str(args.manifest),
                "manifestSha256": manifest_hash,
                "selectedRows": len(selected),
                "selectedBytes": selected_bytes,
                "potentialTargetPairs": len(potential_pairs),
                "potentialPointCeiling": audit["potentialPointCeiling"],
                "rejections": audit["rejections"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
