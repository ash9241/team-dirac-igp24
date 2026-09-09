#!/usr/bin/env python3
"""Reintersect the saved exact SINGLE corpus with the current live ledger.

The historical corpus contains deeply nested JSON artifacts.  Loading all of
them in one Python process can exceed the control machine's memory limit, so
the coordinator runs bounded worker shards and merges only current, novel,
source-validated candidates.  This program has no network or submission path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import stage_single_exact_census as exact


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
INDEX = DATA / "current_exact_archive_reintersection_v2_20260812.jsonl"
AUDIT = DATA / "current_exact_archive_reintersection_v2_20260812_audit.json"
PREFILTER = (
    r'"status"\s*:\s*"(certified|resolved_not_live_signature|hit_staged|'
    r'exact_frozen_gold_hit|exact_signature_miss)[^"]*"|quadratic_twist|'
    r'certified_24T'
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def atomic_new(path: Path, payload: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite sealed output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def candidate_files() -> list[Path]:
    process = subprocess.run(
        [
            "rg",
            "-l",
            PREFILTER,
            str(DATA),
            "-g",
            "*.json",
            "-g",
            "*.jsonl",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return sorted(Path(line).resolve() for line in process.stdout.splitlines() if line)


def current_state(connection: sqlite3.Connection) -> tuple[set, set, set, dict]:
    owned = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND scoreable=1 "
            "AND label IS NOT NULL AND r IS NOT NULL"
        )
    }
    known = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT coefficient_hash FROM polynomials"
        )
    }
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    targets = {
        (str(row["label"]), int(row["r"])): dict(row)
        for row in connection.execute(
            "SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at "
            "FROM targets"
        )
    }
    return owned, known, baseline, targets


def scan_worker(start: int, end: int) -> dict:
    files = candidate_files()
    if not 0 <= start <= end <= len(files):
        raise ValueError("invalid worker slice")
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    owned, known, baseline, targets = current_state(connection)
    candidates: dict[str, dict] = {}
    conflicts = set()
    counts = Counter()
    try:
        for source_path in files[start:end]:
            counts["files"] += 1
            for record_number, root in exact.read_json_roots(source_path):
                for row, pointer, ancestors in exact.iter_nodes(root):
                    if not any(
                        exact.canonical_polynomial_line(row.get(key))
                        for key in exact.PAYLOAD_KEYS
                    ):
                        continue
                    accepted = None
                    for validator in exact.VALIDATORS:
                        accepted = validator(
                            row,
                            ancestors,
                            source_path,
                            record_number,
                            pointer,
                        )
                        if accepted is not None:
                            break
                    if accepted is None:
                        continue
                    counts["exactOccurrences"] += 1
                    if not exact.validate_source_pins(connection, accepted):
                        counts["invalidSourcePins"] += 1
                        continue
                    counts["sourceValidatedOccurrences"] += 1
                    digest = str(accepted["coefficientSha256"])
                    pair = (
                        str(accepted["targetLabel"]),
                        int(accepted["targetR"]),
                    )
                    target = targets.get(pair)
                    if digest in known:
                        counts["knownHash"] += 1
                        continue
                    if pair in owned:
                        counts["ownedPair"] += 1
                        continue
                    if pair in baseline:
                        counts["baselinePair"] += 1
                        continue
                    if target is None:
                        counts["missingTarget"] += 1
                        continue
                    value = {
                        "coefficientBytes": int(accepted["coefficientBytes"]),
                        "coefficientLine": str(accepted["coefficientLine"]),
                        "coefficientSha256": digest,
                        "discovered": bool(target["discovered"]),
                        "proof": accepted["proof"],
                        "sourcePins": accepted["sourcePins"],
                        "targetGeneratedAt": str(target["generated_at"]),
                        "targetLabel": pair[0],
                        "targetMinimumDiscAbs": target["minimum_disc_abs"],
                        "targetR": pair[1],
                        "targetTeamCount": int(target["team_count"]),
                    }
                    incumbent = candidates.get(digest)
                    if incumbent is not None and (
                        incumbent["coefficientLine"] != value["coefficientLine"]
                        or incumbent["targetLabel"] != value["targetLabel"]
                        or incumbent["targetR"] != value["targetR"]
                    ):
                        conflicts.add(digest)
                        candidates.pop(digest, None)
                    elif digest not in conflicts and incumbent is None:
                        candidates[digest] = value
    finally:
        connection.close()
    return {
        "candidates": list(candidates.values()),
        "conflicts": sorted(conflicts),
        "counts": dict(counts),
        "end": end,
        "start": start,
    }


def coordinate(shard_size: int) -> dict:
    files = candidate_files()
    merged: dict[str, dict] = {}
    conflicts = set()
    totals = Counter()
    shards = []
    for start in range(0, len(files), shard_size):
        end = min(len(files), start + shard_size)
        process = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker-start",
                str(start),
                "--worker-end",
                str(end),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(
                f"archive worker {start}:{end} failed with exit "
                f"{process.returncode}: {process.stderr[-2000:]}"
            )
        result = json.loads(process.stdout)
        for key, value in result["counts"].items():
            totals[key] += int(value)
        conflicts.update(result["conflicts"])
        for value in result["candidates"]:
            digest = str(value["coefficientSha256"])
            incumbent = merged.get(digest)
            if incumbent is not None and (
                incumbent["coefficientLine"] != value["coefficientLine"]
                or incumbent["targetLabel"] != value["targetLabel"]
                or incumbent["targetR"] != value["targetR"]
            ):
                conflicts.add(digest)
                merged.pop(digest, None)
            elif digest not in conflicts and incumbent is None:
                merged[digest] = value
        shards.append({
            "candidates": len(result["candidates"]),
            "end": end,
            "start": start,
        })
        print(
            f"archive shard {start}:{end}: "
            f"{len(result['candidates'])} current candidates",
            file=sys.stderr,
            flush=True,
        )

    for digest in conflicts:
        merged.pop(digest, None)
    selected_by_pair: dict[tuple[str, int], dict] = {}
    for value in merged.values():
        pair = (str(value["targetLabel"]), int(value["targetR"]))
        incumbent = selected_by_pair.get(pair)
        rank = (int(value["coefficientBytes"]), str(value["coefficientSha256"]))
        if incumbent is None or rank < (
            int(incumbent["coefficientBytes"]),
            str(incumbent["coefficientSha256"]),
        ):
            selected_by_pair[pair] = value
    selected = sorted(
        selected_by_pair.values(),
        key=lambda row: (
            int(row["targetTeamCount"]),
            int(str(row["targetLabel"])[3:]),
            int(row["targetR"]),
        ),
    )
    index_payload = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in selected
    )
    histogram = Counter(int(row["targetTeamCount"]) for row in selected)
    audit = {
        "schemaVersion": "current-exact-archive-reintersection-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "complete_submission_free",
        "prefilteredArtifactFiles": len(files),
        "workerShardSize": shard_size,
        "workerShards": shards,
        "counts": dict(sorted(totals.items())),
        "conflictingHashesExcluded": len(conflicts),
        "currentNovelExactHashes": len(merged),
        "selectedDistinctPairs": len(selected),
        "teamCountDistribution": {
            str(key): value for key, value in sorted(histogram.items())
        },
        "index": str(INDEX.relative_to(ROOT)),
        "indexSha256": sha256_bytes(index_payload.encode("utf-8")),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_new(INDEX, index_payload)
    atomic_new(AUDIT, json.dumps(audit, indent=2, sort_keys=True) + "\n")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard-size", type=int, default=50)
    parser.add_argument("--worker-start", type=int)
    parser.add_argument("--worker-end", type=int)
    args = parser.parse_args()
    if (args.worker_start is None) != (args.worker_end is None):
        parser.error("worker start and end must be provided together")
    if args.worker_start is not None:
        print(json.dumps(scan_worker(args.worker_start, args.worker_end)))
        return 0
    print(json.dumps(coordinate(args.shard_size), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
