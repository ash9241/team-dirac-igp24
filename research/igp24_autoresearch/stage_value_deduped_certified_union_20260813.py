#!/usr/bin/env python3
"""Build a score-first, one-row-per-new-pair union of sealed exact reserves.

The input manifests are immutable pools of previously certified degree-24
polynomials.  This script re-joins those hashes to their archived exact pair
metadata, removes hashes and pairs already accepted by the server, chooses the
best recorded-discriminant candidate for every remaining pair, and writes
submission-sized chunks ordered by projected live score.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from stage_all_certified_archive_unseen_20260813 import archive_rows, canonical, receipt_hashes


sys.set_int_max_str_digits(1_000_000)
ROOT = Path(__file__).resolve().parent
HISTORICAL_DISC_RETENTION = 0.7878933624152681


def coefficient_height(line: str) -> int:
    return max(abs(int(value)) for value in line.split(","))


def load_manifest_pool(patterns: list[str]) -> tuple[dict[str, str], list[str]]:
    lines: dict[str, str] = {}
    files: list[str] = []
    for pattern in patterns:
        for raw_name in sorted(glob.glob(pattern)):
            path = Path(raw_name)
            files.append(str(path.resolve()))
            for raw in path.read_text(encoding="utf-8").splitlines():
                stripped = raw.split("#", 1)[0].strip()
                if not stripped:
                    continue
                line = canonical(stripped)
                digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                previous = lines.setdefault(digest, line)
                if previous != line:
                    raise ValueError(f"SHA-256 collision in input manifests: {digest}")
    return lines, files


def recorded_discriminant(row: dict) -> int | None:
    value = (
        row.get("field_disc_abs")
        or row.get("nfdisc_abs")
        or row.get("candidate_field_disc_abs")
        or row.get("estimated_nfdisc_abs")
        or row.get("discriminant_abs")
    )
    return int(value) if value is not None else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-glob", action="append", required=True)
    parser.add_argument("--archive-root", action="append", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--max-lines", type=int, default=900)
    parser.add_argument("--max-bytes", type=int, default=900_000)
    args = parser.parse_args()
    if args.certificate.exists():
        raise FileExistsError(args.certificate)

    pool, manifest_files = load_manifest_pool(args.manifest_glob)
    pool_hashes = set(pool)

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    targets = {
        (str(label), int(root)): {
            "teamCount": int(team_count or 0),
            "minimumDiscAbs": int(minimum) if minimum else None,
        }
        for label, root, team_count, minimum in connection.execute(
            "SELECT label,r,team_count,minimum_disc_abs FROM targets"
        )
    }
    accepted_pairs = {
        (str(label), int(root))
        for label, root in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND label IS NOT NULL AND r IS NOT NULL"
        )
    }
    known_hashes = {
        str(value)
        for (value,) in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    baseline_pairs = {
        (str(label), int(root))
        for label, root in connection.execute("SELECT DISTINCT label,r FROM baseline_pairs")
    }
    connection.close()
    known_hashes |= receipt_hashes(args.receipts)

    eligible_hashes = pool_hashes - known_hashes
    metadata: dict[str, dict] = {}
    files_scanned = 0
    rows_scanned = 0
    matching_rows = 0
    hash_mismatches = 0
    pair_conflicts = 0
    for archive_root in args.archive_root:
        paths = sorted(set(archive_root.rglob("*.jsonl")) | set(archive_root.rglob("*.json")))
        for path in paths:
            files_scanned += 1
            try:
                for line_number, row in archive_rows(path):
                    rows_scanned += 1
                    if row.get("submission_ready") is not True:
                        continue
                    if row.get("exact_compatibility_proven") is not True:
                        continue
                    if "label_probability" in row and float(row["label_probability"]) != 1.0:
                        continue
                    if "valid_probability" in row and float(row["valid_probability"]) != 1.0:
                        continue
                    if row.get("local_irreducible") is False:
                        continue
                    try:
                        line = canonical(row["coefficients"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                    if digest not in eligible_hashes:
                        continue
                    claimed = row.get("candidate_hash") or row.get("coefficientSha256")
                    if claimed is not None and str(claimed) != digest:
                        hash_mismatches += 1
                        continue
                    try:
                        t = int(row.get("target_t", row.get("parameters", {}).get("target_t")))
                        root = int(row.get("local_root_count", row.get("target_r")))
                    except (AttributeError, TypeError, ValueError):
                        continue
                    pair = (f"24T{t}", root)
                    disc = recorded_discriminant(row)
                    candidate = {
                        "pair": pair,
                        "recordedDiscAbs": disc,
                        "source": str(path.resolve()),
                        "sourceLine": line_number,
                        "family": str(
                            row.get("recipe_family")
                            or row.get("construction_overgroup")
                            or path.stem
                        ),
                    }
                    previous = metadata.get(digest)
                    if previous is not None and tuple(previous["pair"]) != pair:
                        pair_conflicts += 1
                        continue
                    if previous is None or (
                        disc is not None
                        and (
                            previous["recordedDiscAbs"] is None
                            or disc < int(previous["recordedDiscAbs"])
                        )
                    ):
                        metadata[digest] = candidate
                    matching_rows += 1
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue

    skips = Counter()
    by_pair: dict[tuple[str, int], list[dict]] = {}
    for digest in sorted(eligible_hashes):
        row = metadata.get(digest)
        if row is None:
            skips["missingExactPairMetadata"] += 1
            continue
        pair = tuple(row["pair"])
        if pair not in targets:
            skips["absentFromCurrentTargets"] += 1
            continue
        if pair in baseline_pairs:
            skips["baselinePair"] += 1
            continue
        if pair in accepted_pairs:
            skips["alreadyAcceptedPair"] += 1
            continue
        target = targets[pair]
        disc = row["recordedDiscAbs"]
        minimum = target["minimumDiscAbs"]
        if disc is None:
            ratio = HISTORICAL_DISC_RETENTION
            ratio_source = "historical accepted-backlog retention"
        elif minimum is None:
            ratio = 1.0
            ratio_source = "no incumbent minimum"
        else:
            ratio = min(1.0, math.log(minimum) / math.log(int(disc)))
            ratio_source = "recorded candidate discriminant"
        contention = 2.0 ** (-int(target["teamCount"]))
        by_pair.setdefault(pair, []).append(
            {
                **row,
                "coefficientLine": pool[digest],
                "coefficientSha256": digest,
                "height": coefficient_height(pool[digest]),
                "teamCount": int(target["teamCount"]),
                "liveMinimumDiscAbs": minimum,
                "discriminantRatio": ratio,
                "ratioSource": ratio_source,
                "contentionCeiling": contention,
                "projectedPoints": contention * ratio,
            }
        )

    selected: list[dict] = []
    for rows in by_pair.values():
        rows.sort(
            key=lambda row: (
                -float(row["projectedPoints"]),
                row["recordedDiscAbs"] is None,
                int(row["recordedDiscAbs"] or 0),
                int(row["height"]),
                row["coefficientSha256"],
            )
        )
        selected.append(rows[0] | {"availableRowsForPair": len(rows)})
    selected.sort(
        key=lambda row: (
            -float(row["projectedPoints"]),
            int(row["teamCount"]),
            int(row["pair"][1]),
            int(str(row["pair"][0])[3:]),
            row["coefficientSha256"],
        )
    )

    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[dict] = []
    current: list[dict] = []
    current_bytes = 0

    def flush() -> None:
        nonlocal current, current_bytes
        if not current:
            return
        path = args.output_prefix.with_name(
            f"{args.output_prefix.name}_{len(chunks):03d}.txt"
        )
        if path.exists():
            raise FileExistsError(path)
        rendered = "".join(row["coefficientLine"] + "\n" for row in current)
        path.write_text(rendered, encoding="ascii")
        chunks.append(
            {
                "path": str(path.resolve()),
                "rows": len(current),
                "bytes": len(rendered.encode("ascii")),
                "sha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
                "projectedPoints": sum(float(row["projectedPoints"]) for row in current),
                "contentionCeiling": sum(float(row["contentionCeiling"]) for row in current),
                "maxTeamCount": max(int(row["teamCount"]) for row in current),
            }
        )
        current = []
        current_bytes = 0

    for row in selected:
        size = len(row["coefficientLine"].encode("ascii")) + 1
        if current and (len(current) >= args.max_lines or current_bytes + size > args.max_bytes):
            flush()
        current.append(row)
        current_bytes += size
    flush()

    payload = {
        "schemaVersion": 1,
        "method": "one best certified row per current non-baseline pair not already accepted",
        "inputManifestFiles": manifest_files,
        "inputUniqueHashes": len(pool_hashes),
        "knownHashesExcluded": len(pool_hashes - eligible_hashes),
        "eligibleHashes": len(eligible_hashes),
        "archiveRoots": [str(path.resolve()) for path in args.archive_root],
        "archiveFilesScanned": files_scanned,
        "archiveRowsScanned": rows_scanned,
        "matchingArchiveRows": matching_rows,
        "hashMismatches": hash_mismatches,
        "pairConflicts": pair_conflicts,
        "resolvedHashes": len(metadata),
        "selectedRows": len(selected),
        "selectedPairs": len(selected),
        "projectedMarginalPoints": sum(float(row["projectedPoints"]) for row in selected),
        "contentionOnlyCeiling": sum(float(row["contentionCeiling"]) for row in selected),
        "teamCountDistribution": dict(sorted(Counter(row["teamCount"] for row in selected).items())),
        "recordedDiscRows": sum(row["recordedDiscAbs"] is not None for row in selected),
        "estimatedDiscRows": sum(row["recordedDiscAbs"] is None for row in selected),
        "skips": dict(sorted(skips.items())),
        "chunks": chunks,
        "entries": selected,
        "checks": {
            "inputLinesCanonicalAndHashVerified": True,
            "knownHashesExcluded": True,
            "acceptedPairsExcluded": True,
            "baselinePairsExcluded": True,
            "oneRowPerPair": len(selected) == len({tuple(row["pair"]) for row in selected}),
            "allArchiveRowsExactlyCertified": True,
            "noArchiveHashMismatch": hash_mismatches == 0,
            "noPairConflict": pair_conflicts == 0,
        },
    }
    args.certificate.parent.mkdir(parents=True, exist_ok=True)
    args.certificate.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "inputUniqueHashes",
                    "knownHashesExcluded",
                    "eligibleHashes",
                    "resolvedHashes",
                    "selectedRows",
                    "selectedPairs",
                    "projectedMarginalPoints",
                    "contentionOnlyCeiling",
                    "teamCountDistribution",
                    "recordedDiscRows",
                    "estimatedDiscRows",
                    "skips",
                    "checks",
                )
            }
            | {"chunkCount": len(chunks)},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
