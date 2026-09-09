#!/usr/bin/env python3
"""Estimate and rank score gain from the sealed exact archive reserve."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path

from stage_all_certified_archive_unseen_20260813 import archive_rows, canonical


sys.set_int_max_str_digits(1_000_000)
ROOT = Path(__file__).resolve().parent


def manifest_hashes(pattern: str) -> tuple[set[str], dict[str, str]]:
    hashes = set()
    lines = {}
    for name in glob.glob(pattern):
        for raw in Path(name).read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            line = canonical(raw)
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            hashes.add(digest)
            lines[digest] = line
    return hashes, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest-glob",
        action="append",
        default=None,
    )
    parser.add_argument("--archive-root", action="append", type=Path)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--priority-prefix", type=Path, required=True)
    parser.add_argument("--max-lines", type=int, default=900)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)

    patterns = args.manifest_glob or [
        str(ROOT / "outbox" / "all_certified_archive_unseen_20260813_*.txt")
    ]
    all_hashes: set[str] = set()
    lines: dict[str, str] = {}
    for pattern in patterns:
        pattern_hashes, pattern_lines = manifest_hashes(pattern)
        all_hashes |= pattern_hashes
        lines.update(pattern_lines)
    accepted = set()
    for receipt_path in (ROOT / "receipts").glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt.get("manifest", "")))
            if "all_certified_archive_unseen_20260813_" not in manifest.name:
                continue
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                if raw.strip():
                    line = canonical(raw)
                    accepted.add(hashlib.sha256(line.encode("ascii")).hexdigest())
        except (OSError, ValueError, json.JSONDecodeError):
            continue

    # One immutable hash can appear in several archive copies. Retain its
    # smallest recorded exact/estimated nfdisc and require a unique pair.
    candidates: dict[str, dict] = {}
    archive_roots = args.archive_root or [ROOT.parent / "routeA" / "data", ROOT / "data"]
    for archive_root in archive_roots:
        paths = sorted(set(archive_root.rglob("*.jsonl")) | set(archive_root.rglob("*.json")))
        for path in paths:
            try:
                for line_number, row in archive_rows(path):
                    if row.get("submission_ready") is not True:
                        continue
                    if row.get("exact_compatibility_proven") is not True:
                        continue
                    try:
                        line = canonical(row["coefficients"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                    if digest not in all_hashes:
                        continue
                    t = row.get("target_t")
                    root = row.get("local_root_count", row.get("target_r"))
                    disc = (
                        row.get("field_disc_abs")
                        or row.get("nfdisc_abs")
                        or row.get("candidate_field_disc_abs")
                        or row.get("estimated_nfdisc_abs")
                        or row.get("discriminant_abs")
                    )
                    if t is None or root is None or disc is None:
                        continue
                    pair = (f"24T{int(t)}", int(root))
                    value = int(disc)
                    previous = candidates.get(digest)
                    if previous is None:
                        candidates[digest] = {
                            "pair": pair,
                            "recordedDiscAbs": value,
                            "source": str(path.resolve()),
                            "sourceLine": line_number,
                        }
                    elif tuple(previous["pair"]) != pair:
                        raise ValueError(f"archive pair conflict for {digest}")
                    elif value < int(previous["recordedDiscAbs"]):
                        previous.update(
                            recordedDiscAbs=value,
                            source=str(path.resolve()),
                            sourceLine=line_number,
                        )
            except (OSError, UnicodeDecodeError):
                continue

    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as connection:
        targets = {
            (str(label), int(root)): {
                "teamCount": int(team_count),
                "minimumDiscAbs": int(minimum) if minimum else None,
            }
            for label, root, team_count, minimum in connection.execute(
                "SELECT label,r,team_count,minimum_disc_abs FROM targets"
            )
        }
        our_best: dict[tuple[str, int], int] = {}
        for label, root, disc in connection.execute(
            "SELECT label,r,field_disc_abs FROM verifications "
            "WHERE status='accepted' AND field_disc_abs IS NOT NULL"
        ):
            pair = str(label), int(root)
            value = int(disc)
            our_best[pair] = min(value, our_best.get(pair, value))

    def gain(row: dict) -> float:
        pair = tuple(row["pair"])
        target = targets.get(pair)
        old_disc = our_best.get(pair)
        candidate_disc = int(row["recordedDiscAbs"])
        if target is None or old_disc is None or candidate_disc >= old_disc:
            return 0.0
        team_count = int(target["teamCount"])
        incumbent = target["minimumDiscAbs"]
        if team_count == 0 or incumbent is None:
            return 0.0
        old_ratio = min(1.0, math.log(incumbent) / math.log(old_disc))
        new_ratio = min(1.0, math.log(incumbent) / math.log(candidate_disc))
        return (2.0 ** (-team_count)) * max(0.0, new_ratio - old_ratio)

    remaining = all_hashes - accepted
    ranked = []
    for digest in remaining:
        row = candidates.get(digest)
        if row is None:
            continue
        pair = tuple(row["pair"])
        old_disc = our_best.get(pair)
        target = targets.get(pair)
        row = {
            **row,
            "coefficientSha256": digest,
            "ourBestFieldDiscAbs": old_disc,
            "teamCount": target["teamCount"] if target else None,
            "liveMinimumDiscAbs": target["minimumDiscAbs"] if target else None,
        }
        row["estimatedIncrementalGain"] = gain(row)
        row["improvesOurBest"] = old_disc is not None and int(row["recordedDiscAbs"]) < old_disc
        ranked.append(row)
    ranked.sort(
        key=lambda row: (
            -float(row["estimatedIncrementalGain"]),
            not bool(row["improvesOurBest"]),
            int(row["teamCount"] if row["teamCount"] is not None else 10**9),
            int(row["recordedDiscAbs"]),
            row["coefficientSha256"],
        )
    )

    # Keep every remaining row, but put provable/recorded improvements first.
    priority = [row["coefficientSha256"] for row in ranked]
    priority.extend(sorted(remaining - set(priority)))
    args.priority_prefix.parent.mkdir(parents=True, exist_ok=True)

    improving = [row for row in ranked if row["improvesOurBest"]]
    positive_all = [row for row in ranked if float(row["estimatedIncrementalGain"]) > 0]
    # Only the best submitted field for a pair can improve its score.  Several
    # immutable polynomials for the same pair are alternatives, not additive
    # gains, so report and prioritize one best candidate per pair.
    positive_by_pair = {}
    for row in positive_all:
        pair = tuple(row["pair"])
        incumbent = positive_by_pair.get(pair)
        if incumbent is None or (
            float(row["estimatedIncrementalGain"]), -int(row["recordedDiscAbs"])
        ) > (
            float(incumbent["estimatedIncrementalGain"]),
            -int(incumbent["recordedDiscAbs"]),
        ):
            positive_by_pair[pair] = row
    positive = sorted(
        positive_by_pair.values(),
        key=lambda row: (
            -float(row["estimatedIncrementalGain"]),
            int(row["recordedDiscAbs"]),
            row["coefficientSha256"],
        ),
    )
    positive_hashes = [row["coefficientSha256"] for row in positive]
    priority = positive_hashes + [digest for digest in priority if digest not in set(positive_hashes)]

    # Write the one-per-pair positive prefix followed by the complete tail.
    chunks = []
    for start in range(0, len(priority), args.max_lines):
        chunk_hashes = priority[start : start + args.max_lines]
        path = args.priority_prefix.with_name(
            f"{args.priority_prefix.name}_{len(chunks):03d}.txt"
        )
        rendered = "".join(lines[digest] + "\n" for digest in chunk_hashes)
        path.write_text(rendered, encoding="utf-8")
        chunks.append(
            {
                "path": str(path.resolve()),
                "rows": len(chunk_hashes),
                "bytes": len(rendered.encode("ascii")),
                "sha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
            }
        )
    payload = {
        "allSealedHashes": len(all_hashes),
        "alreadyAcceptedHashes": len(accepted),
        "remainingHashes": len(remaining),
        "remainingWithRecordedDiscriminant": len(ranked),
        "improveOurBestPairs": len({tuple(row["pair"]) for row in improving}),
        "positiveEstimatedGainPairs": len({tuple(row["pair"]) for row in positive}),
        "estimatedIncrementalGain": sum(float(row["estimatedIncrementalGain"]) for row in positive),
        "priorityChunks": chunks,
        "topPositiveRows": positive[:500],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in payload.items() if key not in {"topPositiveRows", "priorityChunks"}}
            | {"priorityChunkCount": len(chunks)},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
