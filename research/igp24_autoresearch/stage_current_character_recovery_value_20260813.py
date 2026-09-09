#!/usr/bin/env python3
"""Fail-closed stager for exact character-recovery output artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def canonical(value: str) -> str:
    parts = [int(part) for part in value.split(",")]
    if len(parts) != 25 or parts[-1] != 1:
        raise ValueError("candidate is not a monic degree-24 polynomial")
    return ",".join(map(str, parts))


def receipt_hashes(path: Path) -> set[str]:
    output = set()
    for receipt_path in path.glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            if not manifest.is_file():
                continue
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != str(receipt["manifestHash"]):
                continue
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    output.add(hashlib.sha256(canonical(line).encode("ascii")).hexdigest())
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--max-team-count", type=int, default=3)
    parser.add_argument(
        "--exclude-pair",
        action="append",
        default=[],
        metavar="24TNNNN,R",
        help="exclude a label/root-count pair already pending in another submission",
    )
    parser.add_argument(
        "--exclude-certificate",
        action="append",
        type=Path,
        default=[],
        help="exclude every selected label/root pair in an earlier stage certificate",
    )
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite character recovery stage")

    paths = []
    for pattern in args.input:
        matches = sorted(ROOT.glob(pattern))
        if not matches:
            raise FileNotFoundError(pattern)
        paths.extend(matches)
    pending = receipt_hashes(args.receipts)
    excluded_pairs = set()
    for value in args.exclude_pair:
        label, separator, root = value.partition(",")
        if not separator or not label.startswith("24T"):
            raise ValueError(f"invalid --exclude-pair value: {value}")
        excluded_pairs.add((label, int(root)))
    for path in args.exclude_certificate:
        certificate = json.loads(path.read_text(encoding="utf-8"))
        for row in certificate.get("selectedPairs") or []:
            excluded_pairs.add((str(row["label"]), int(row["r"])))
    skips = Counter()
    eligible = []
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for path in paths:
            artifact = json.loads(path.read_text(encoding="utf-8"))
            if artifact.get("status") != "complete":
                skips["incomplete_artifact"] += 1
                continue
            for row in artifact.get("candidates", []):
                if not (
                    row.get("submission_ready") is True
                    and row.get("exact_compatibility_proven") is True
                    and float(row.get("label_probability", 0.0)) == 1.0
                    and float(row.get("valid_probability", 0.0)) == 1.0
                ):
                    skips["not_exact_submission_ready"] += 1
                    continue
                basis = str(row.get("serverCalibrationBasis", ""))
                if basis not in {"accepted_same_arithmetic_family", "full_character_group"}:
                    skips["missing_server_calibration"] += 1
                    continue
                line = canonical(str(row["coefficients"]))
                digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                if digest != str(row["candidate_hash"]):
                    raise ValueError(f"candidate hash mismatch in {path}")
                label = f"24T{int(row['target_t'])}"
                root = int(row["target_r"])
                if int(row.get("local_root_count", -1)) != root:
                    raise ValueError(f"root-count mismatch in {path}")
                pair = label, root
                if pair in excluded_pairs:
                    skips["excluded_pending_pair"] += 1
                    continue
                if digest in pending:
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
                if int(target["team_count"]) > args.max_team_count:
                    skips["above_team_count_cap"] += 1
                    continue
                eligible.append(
                    {
                        "artifact": str(path.relative_to(ROOT)),
                        "basis": basis,
                        "coefficientLine": line,
                        "coefficientSha256": digest,
                        "fieldDiscriminantAbs": str(row["field_disc_abs"]),
                        "label": label,
                        "r": root,
                        "teamCount": int(target["team_count"]),
                        "targetGeneratedAt": str(target["generated_at"]),
                        "targetMinimumDiscAbs": target["minimum_disc_abs"],
                    }
                )
    finally:
        connection.close()

    selected_by_pair = {}
    for row in eligible:
        pair = row["label"], row["r"]
        rank = (
            int(row["fieldDiscriminantAbs"]),
            len(row["coefficientLine"]),
            row["coefficientSha256"],
        )
        incumbent = selected_by_pair.get(pair)
        if incumbent is None:
            selected_by_pair[pair] = row
        else:
            skips["duplicate_pair"] += 1
            old_rank = (
                int(incumbent["fieldDiscriminantAbs"]),
                len(incumbent["coefficientLine"]),
                incumbent["coefficientSha256"],
            )
            if rank < old_rank:
                selected_by_pair[pair] = row
    selected = sorted(
        selected_by_pair.values(),
        key=lambda row: (row["teamCount"], int(row["label"][3:]), row["r"]),
    )
    if not selected:
        raise ValueError("no current character-recovery value candidates remain")
    projection = sum(
        (Fraction(1, 2 ** row["teamCount"]) for row in selected),
        Fraction(0, 1),
    )
    manifest = "".join(row["coefficientLine"] + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    summary = {
        "inputs": [str(path.relative_to(ROOT)) for path in paths],
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "projectedMarginalScoreExact": str(projection),
        "projectionQualifier": "before discriminant penalty",
        "selectedRows": len(selected),
        "selectedPairs": selected,
        "skipCounts": dict(sorted(skips.items())),
        "teamCountDistribution": dict(sorted(Counter(row["teamCount"] for row in selected).items())),
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
