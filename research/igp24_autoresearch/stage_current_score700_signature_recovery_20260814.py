#!/usr/bin/env python3
"""Fail-closed staging for exact score700 signature-recovery results."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def canonical_line(value: object) -> str | None:
    if isinstance(value, str):
        raw = value.strip().split(",")
    elif isinstance(value, list):
        raw = value
    else:
        return None
    try:
        coefficients = [int(entry) for entry in raw]
    except (TypeError, ValueError):
        return None
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or coefficients[0] == 0
        or math.gcd(*coefficients) != 1
    ):
        return None
    return ",".join(str(entry) for entry in coefficients)


def line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def manifest_hashes(paths: list[Path]) -> set[str]:
    result: set[str] = set()
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for raw in lines:
            line = canonical_line(raw.split("#", 1)[0].strip())
            if line is not None:
                result.add(line_hash(line))
    return result


def receipt_manifest_paths(receipts: Path) -> list[Path]:
    paths: list[Path] = []
    for receipt_path in receipts.glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            recorded = str(receipt["manifestHash"])
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
        if not manifest.is_absolute():
            manifest = ROOT / manifest
        if (
            manifest.is_file()
            and SHA_RE.fullmatch(recorded)
            and hashlib.sha256(manifest.read_bytes()).hexdigest() == recorded
        ):
            paths.append(manifest)
    return paths


def resolve_inputs(patterns: list[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        absolute = pattern if Path(pattern).is_absolute() else str(ROOT / pattern)
        matches = [Path(value).resolve() for value in glob.glob(absolute)]
        if not matches:
            raise FileNotFoundError(pattern)
        paths.update(path for path in matches if path.is_file())
    return sorted(paths)


def exact_candidate(root: dict, row: object, path: Path) -> tuple[dict | None, str]:
    if not isinstance(row, dict):
        return None, "candidate_not_object"
    line = canonical_line(row.get("coefficients"))
    if line is None:
        return None, "invalid_polynomial"
    digest = line_hash(line)
    if digest != str(row.get("candidate_hash", "")):
        raise ValueError(f"candidate hash mismatch in {path}")
    try:
        target_t = int(row["target_t"])
        offline_t = int(row["offlineCertifiedTargetT"])
        target_r = int(row["target_r"])
        local_r = int(row["local_root_count"])
        field_disc = int(row["field_disc_abs"])
    except (KeyError, TypeError, ValueError):
        return None, "invalid_exact_fields"
    label = f"24T{target_t}"
    live = row.get("liveTarget")
    job = root.get("job")
    if (
        root.get("status") != "complete"
        or row.get("submission_ready") is not True
        or row.get("exact_compatibility_proven") is not True
        or row.get("local_irreducible") is not True
        or float(row.get("valid_probability", 0.0)) != 1.0
        or float(row.get("label_probability", 0.0)) != 1.0
        or row.get("serverCalibrationBasis") != "accepted_same_arithmetic_family"
        or offline_t != target_t
        or LABEL_RE.fullmatch(label) is None
        or target_r != local_r
        or target_r < 0
        or target_r > 24
        or target_r % 2
        or field_disc <= 0
        or not isinstance(live, dict)
        or str(live.get("label")) != label
        or int(live.get("r", -1)) != target_r
        or not isinstance(job, dict)
        or str(row.get("sourceJobId")) != str(job.get("jobId"))
        or int(row.get("sourceArchiveLine", -1)) != int(job.get("sourceArchiveLine", -2))
        or int(job.get("terminalCalibration", {}).get(str(target_t), -1)) != target_t
        or not str(row.get("construction_overgroup", "")).startswith(
            "exact_transitive_subgroup_descent:"
        )
        or not isinstance(row.get("maximal_subgroups_excluded"), list)
        or not row["maximal_subgroups_excluded"]
        or not isinstance(row.get("modular_witnesses"), list)
        or not row["modular_witnesses"]
    ):
        return None, "incomplete_exact_certificate"
    matching_attempts = [
        attempt
        for attempt in root.get("attempts", [])
        if isinstance(attempt, dict)
        and attempt.get("status") == "exact_live_candidate"
        and int(attempt.get("root", -1)) == target_r
        and int(attempt.get("terminalT", -1)) == offline_t
        and int(attempt.get("serverCalibratedTerminalT", -1)) == target_t
    ]
    if len(matching_attempts) != 1:
        return None, "missing_unique_exact_attempt"
    return {
        "coefficientLine": line,
        "coefficientSha256": digest,
        "fieldDiscriminantAbs": str(field_disc),
        "label": label,
        "r": target_r,
        "artifact": str(path.relative_to(ROOT)),
        "sourceJobId": str(row["sourceJobId"]),
        "sourceArchiveLine": int(row["sourceArchiveLine"]),
        "certificateSchema": "score700_integral_basis_exact_subgroup_v1",
    }, "accepted"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--outbox", type=Path, default=ROOT / "outbox")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--max-team-count", type=int, default=6)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite staged recovery artifacts")

    paths = resolve_inputs(args.input)
    pending_paths = receipt_manifest_paths(args.receipts)
    pending_paths.extend(path for path in args.outbox.rglob("*") if path.is_file())
    excluded_hashes = manifest_hashes(pending_paths)
    skips: Counter[str] = Counter()
    candidates: list[dict] = []

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for path in paths:
            try:
                root = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}: {exc}") from exc
            if not isinstance(root, dict) or not isinstance(root.get("candidates"), list):
                skips["invalid_result_schema"] += 1
                continue
            for row in root["candidates"]:
                candidate, status = exact_candidate(root, row, path)
                if candidate is None:
                    skips[status] += 1
                    continue
                digest = candidate["coefficientSha256"]
                pair = (candidate["label"], candidate["r"])
                if digest in excluded_hashes:
                    skips["receipt_or_outbox_hash"] += 1
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
                    skips["missing_live_target"] += 1
                    continue
                team_count = int(target["team_count"])
                if team_count > args.max_team_count:
                    skips["above_team_count_cap"] += 1
                    continue
                candidate.update(
                    {
                        "teamCount": team_count,
                        "targetMinimumDiscAbs": str(target["minimum_disc_abs"]),
                        "targetGeneratedAt": str(target["generated_at"]),
                    }
                )
                candidates.append(candidate)
    finally:
        connection.close()

    best: dict[tuple[str, int], dict] = {}
    for row in candidates:
        pair = (row["label"], row["r"])
        incumbent = best.get(pair)
        rank = (
            int(row["fieldDiscriminantAbs"]),
            len(row["coefficientLine"]),
            row["coefficientSha256"],
        )
        if incumbent is None:
            best[pair] = row
            continue
        skips["duplicate_pair"] += 1
        incumbent_rank = (
            int(incumbent["fieldDiscriminantAbs"]),
            len(incumbent["coefficientLine"]),
            incumbent["coefficientSha256"],
        )
        if rank < incumbent_rank:
            best[pair] = row

    selected = sorted(
        best.values(),
        key=lambda row: (row["teamCount"], int(row["label"][3:]), row["r"]),
    )
    if not selected:
        raise ValueError("no current exact score700 recovery candidates remain")
    projection = sum(
        (Fraction(1, 2 ** row["teamCount"]) for row in selected), Fraction(0, 1)
    )
    manifest = "".join(row["coefficientLine"] + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    summary = {
        "certificateSchema": "score700_signature_recovery_stage_v1",
        "inputs": [str(path.relative_to(ROOT)) for path in paths],
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "projectedMarginalScoreExact": str(projection),
        "projectionQualifier": "before discriminant penalty",
        "selectedPairs": [
            {key: row[key] for key in (
                "artifact",
                "certificateSchema",
                "coefficientSha256",
                "fieldDiscriminantAbs",
                "label",
                "r",
                "sourceArchiveLine",
                "sourceJobId",
                "targetGeneratedAt",
                "targetMinimumDiscAbs",
                "teamCount",
            )}
            for row in selected
        ],
        "selectedRows": len(selected),
        "skipCounts": dict(sorted(skips.items())),
        "teamCountDistribution": dict(
            sorted(Counter(row["teamCount"] for row in selected).items())
        ),
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
