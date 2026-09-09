#!/usr/bin/env python3
"""Freeze a disjoint exact pair-sibling frontier from the Jul 7--9 backfill."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ORBIT_MAP = DATA / "pair_orbit_map.jsonl"
ADDITIONAL_ORBIT_MAP = DATA / "agent_gold_b_backfill_pair_orbits.jsonl"
PROFILE_INPUTS = (
    DATA / "pair_signature_map.jsonl",
    DATA / "rank12_route_signature_profiles.jsonl",
    DATA / "agent_pair_sibling_shard2_profiles.jsonl",
)
PROFILE_CACHE = DATA / "agent_gold_b_backfill_profiles.jsonl"
PROFILE_WORKER = ROOT / "pair_signature_one.sage.py"
FRONTIER = DATA / "agent_gold_b_backfill_closure_frontier.jsonl"
SUMMARY = DATA / "agent_gold_b_backfill_closure_frontier_summary.json"
OLD_CUTOFF = 1784628497.513
HISTORICAL_CREATED_BEFORE = "2026-07-21T00:00:00Z"
RANK10_EVIDENCE = Path(
    "/private/tmp/rank10_raid/low_hanging_fruit_unique_placements.jsonl"
)
RANK10_TEAM_ID = "teamv2_07f0f7f581c34fd1a0dd913dad90dcec"
EXCLUDED_SOURCE_ARTIFACTS = (
    DATA / "agent_gold_a_cross100_forced_frontier.jsonl",
    DATA / "agent_pair_sibling_exact_frontier_shard2.jsonl",
    DATA / "agent_gold_a_cross100_batch_results.jsonl",
    DATA / "agent_pair_sibling_shard2_results.jsonl",
    DATA / "agent_gold_a_cross100_forced_hits.jsonl",
    DATA / "pair_sum_candidates.jsonl",
    DATA / "pair_sum_single_expanded_candidates.jsonl",
)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_key(row: dict) -> tuple[str, int] | None:
    submission = row.get("sourceSubmissionId", row.get("submissionId"))
    index = row.get("sourcePolynomialIndex", row.get("polynomialIndex"))
    if submission is None or index is None:
        return None
    return str(submission), int(index)


def unique_orbits() -> dict[str, dict]:
    result = {}
    for path in (ORBIT_MAP, ADDITIONAL_ORBIT_MAP):
        for row in read_jsonl(path):
            targets = list(row.get("targets") or [])
            if len(targets) != 1 or str(targets[0]["targetLabel"]) == str(row["sourceLabel"]):
                continue
            result[str(row["sourceLabel"])] = row
    return result


def load_profiles() -> dict[str, dict]:
    result = {}
    for path in (*PROFILE_INPUTS, PROFILE_CACHE):
        for row in read_jsonl(path):
            if row.get("status") not in (None, "certified"):
                continue
            result[str(row["sourceLabel"])] = row
    return result


def compute_profile(label: str, t: int, timeout: int) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            ["sage", "-python", str(PROFILE_WORKER), label, str(t)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"sourceLabel": label, "sourceT": t, "status": "timeout", "wallSeconds": round(time.monotonic() - started, 3)}
    if completed.returncode != 0:
        return {
            "sourceLabel": label,
            "sourceT": t,
            "status": "error",
            "stderrTail": completed.stderr[-1200:],
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    try:
        row = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {"sourceLabel": label, "sourceT": t, "status": "invalid_output", "error": str(exc)}
    row["status"] = "certified"
    row["wallSeconds"] = round(time.monotonic() - started, 3)
    return row


def valuable_pairs(connection: sqlite3.Connection) -> tuple[dict[tuple[str, int], dict], set[tuple[str, int]]]:
    baseline = {(str(row[0]), int(row[1])) for row in connection.execute("SELECT label,r FROM baseline_pairs")}
    owned = {
        (str(row[0]), int(row[1]))
        for row in connection.execute("SELECT DISTINCT label,r FROM verifications WHERE scoreable=1")
    }
    target_counts = {
        (str(row[0]), int(row[1])): int(row[2])
        for row in connection.execute("SELECT label,r,team_count FROM targets")
    }
    raids = {}
    for row in read_jsonl(RANK10_EVIDENCE):
        if str(row.get("teamId")) != RANK10_TEAM_ID or int(row.get("kTeams", 0)) != 1:
            continue
        pair = (str(row["label"]), int(row["r"]))
        if pair not in baseline and pair not in owned and target_counts.get(pair) == 1:
            raids[pair] = row
    gold = {
        pair
        for pair, team_count in target_counts.items()
        if team_count == 0 and pair not in baseline and pair not in owned
    }
    return raids, gold


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--profile-timeout", type=int, default=60)
    parser.add_argument("--max-fields-per-source-pair", type=int, default=0)
    parser.add_argument("--snapshot-upper", type=float)
    args = parser.parse_args()
    if args.workers < 1 or args.profile_timeout < 1 or args.max_fields_per_source_pair < 0:
        parser.error("worker/timeout must be positive and field cap nonnegative")

    orbits = unique_orbits()
    excluded_keys = {
        key
        for path in EXCLUDED_SOURCE_ARTIFACTS
        for row in read_jsonl(path)
        for key in [source_key(row)]
        if key is not None
    }
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        upper = (
            float(args.snapshot_upper)
            if args.snapshot_upper is not None
            else float(
                connection.execute(
                    "SELECT MAX(synced_at) FROM submissions WHERE synced_at>? AND created_at<?",
                    (OLD_CUTOFF, HISTORICAL_CREATED_BEFORE),
                ).fetchone()[0]
            )
        )
        raids, gold = valuable_pairs(connection)
        valuable = set(raids) | gold
        valuable_labels = {label for label, _r in valuable}
        raw_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT p.submission_id,p.polynomial_index,p.coefficient_hash,
                       length(p.coefficients) AS coefficient_bytes,
                       v.label,v.t,v.r,v.field_disc_abs,s.created_at,s.synced_at
                FROM verifications AS v
                JOIN polynomials AS p USING(submission_id,polynomial_index)
                JOIN submissions AS s USING(submission_id)
                WHERE s.synced_at>? AND s.synced_at<=? AND s.created_at<?
                  AND v.scoreable=1
                ORDER BY s.synced_at,p.submission_id,p.polynomial_index
                """,
                (OLD_CUTOFF, upper, HISTORICAL_CREATED_BEFORE),
            )
        ]
    finally:
        connection.close()

    broad = []
    for row in raw_rows:
        key = (str(row["submission_id"]), int(row["polynomial_index"]))
        if key in excluded_keys:
            continue
        orbit = orbits.get(str(row["label"]))
        if orbit is None:
            continue
        target_label = str(orbit["targets"][0]["targetLabel"])
        if target_label not in valuable_labels:
            continue
        broad.append(row)

    profiles = load_profiles()
    needed = sorted(
        {
            (str(row["label"]), int(row["t"]))
            for row in broad
            if str(row["label"]) not in profiles
        },
        key=lambda item: item[1],
    )
    computed = []
    if needed:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(compute_profile, label, t, args.profile_timeout): (label, t)
                for label, t in needed
            }
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                row = future.result()
                computed.append(row)
                print(
                    f"profile {index}/{len(needed)} {row['sourceLabel']} status={row['status']}",
                    flush=True,
                )
        cached = read_jsonl(PROFILE_CACHE)
        by_label = {str(row["sourceLabel"]): row for row in [*cached, *computed]}
        write_jsonl_atomic(
            PROFILE_CACHE,
            sorted(by_label.values(), key=lambda row: int(row["sourceT"])),
        )
        profiles = load_profiles()

    candidates = []
    skip_counts: Counter = Counter()
    for row in broad:
        profile = profiles.get(str(row["label"]))
        if profile is None:
            skip_counts["profile_missing"] += 1
            continue
        compatible = [
            item
            for item in profile.get("profiles") or []
            if int(item["sourceR"]) == int(row["r"])
        ]
        if not compatible:
            skip_counts["no_compatible_complex_conjugation_class"] += 1
            continue
        realized_pairs = {
            (str(item["orbitSignatures"][0]["targetLabel"]), int(item["orbitSignatures"][0]["targetR"]))
            for item in compatible
        }
        valuable_realizations = realized_pairs & valuable
        if not valuable_realizations:
            skip_counts["no_valuable_compatible_signature"] += 1
            continue
        raid_realizations = valuable_realizations & set(raids)
        gold_realizations = valuable_realizations & gold
        target_label = str(orbits[str(row["label"])]["targets"][0]["targetLabel"])
        forced = len(realized_pairs) == 1 and realized_pairs <= valuable
        if forced and raid_realizations:
            priority_tier, priority_kind = 0, "forced_rank10_solo_raid"
        elif raid_realizations:
            priority_tier, priority_kind = 1, "possible_rank10_solo_raid"
        elif forced and gold_realizations:
            priority_tier, priority_kind = 2, "forced_live_gold"
        else:
            priority_tier, priority_kind = 3, "possible_live_gold"
        candidates.append(
            {
                "submissionId": str(row["submission_id"]),
                "polynomialIndex": int(row["polynomial_index"]),
                "sourceLabel": str(row["label"]),
                "sourceT": int(row["t"]),
                "sourceR": int(row["r"]),
                "sourceCoefficientBytes": int(row["coefficient_bytes"]),
                "sourceCoefficientSha256": str(row["coefficient_hash"]),
                "sourceFieldDiscAbs": str(row["field_disc_abs"]),
                "sourceCreatedAt": str(row["created_at"]),
                "sourceSyncedAt": float(row["synced_at"]),
                "targetLabel": target_label,
                "priorityTier": priority_tier,
                "priorityKind": priority_kind,
                "forcedExactPairAcrossCompatibleClasses": forced,
                "compatibleClassIndexes": sorted(int(item["classIndex"]) for item in compatible),
                "possibleTargetPairs": [
                    {"label": label, "r": r, "kind": "rank10_solo_raid" if (label, r) in raids else "live_gold"}
                    for label, r in sorted(valuable_realizations, key=lambda pair: (pair not in raids, int(pair[0][3:]), pair[1]))
                ],
                "snapshotOldCutoffEpoch": OLD_CUTOFF,
                "snapshotUpperEpoch": upper,
            }
        )

    # One defining polynomial per recovered field-discriminant/source pair is
    # enough for the exact conjugacy-class test; retain the shortest primitive
    # element deterministically.  The optional cap is applied after this gate.
    best_by_field = {}
    for row in candidates:
        key = (row["sourceLabel"], row["sourceR"], row["sourceFieldDiscAbs"])
        order = (
            row["priorityTier"],
            row["sourceCoefficientBytes"],
            row["sourceCoefficientSha256"],
            row["submissionId"],
            row["polynomialIndex"],
        )
        incumbent = best_by_field.get(key)
        if incumbent is None or order < incumbent[0]:
            best_by_field[key] = (order, row)
    frontier = [value[1] for value in best_by_field.values()]
    frontier.sort(
        key=lambda row: (
            row["priorityTier"],
            not row["forcedExactPairAcrossCompatibleClasses"],
            row["sourceCoefficientBytes"],
            int(row["targetLabel"][3:]),
            int(row["sourceLabel"][3:]),
            row["sourceR"],
            row["sourceFieldDiscAbs"],
            row["sourceCoefficientSha256"],
        )
    )
    if args.max_fields_per_source_pair:
        retained = []
        counts: Counter = Counter()
        for row in frontier:
            key = (row["sourceLabel"], row["sourceR"])
            if counts[key] >= args.max_fields_per_source_pair:
                continue
            counts[key] += 1
            retained.append(row)
        frontier = retained
    for rank, row in enumerate(frontier, start=1):
        row["backfillClosureRank"] = rank

    write_jsonl_atomic(FRONTIER, frontier)
    summary = {
        "oldCutoffEpochExclusive": OLD_CUTOFF,
        "snapshotUpperEpochInclusive": upper,
        "historicalCreatedBefore": HISTORICAL_CREATED_BEFORE,
        "postCutoffScoreableRows": len(raw_rows),
        "excludedSourceKeys": len(excluded_keys),
        "mappedUniqueNonselfLabels": len(orbits),
        "broadValuableLabelRows": len(broad),
        "profilesComputed": len(computed),
        "profileCache": str(PROFILE_CACHE),
        "profileCacheSha256": sha256_file(PROFILE_CACHE) if PROFILE_CACHE.exists() else None,
        "candidateRowsBeforeFieldDedup": len(candidates),
        "frontierRows": len(frontier),
        "frontierSourcePairs": len({(row['sourceLabel'], row['sourceR']) for row in frontier}),
        "frontierSourceLabels": len({row['sourceLabel'] for row in frontier}),
        "priorityCounts": dict(sorted(Counter(row["priorityKind"] for row in frontier).items())),
        "skipCounts": dict(sorted(skip_counts.items())),
        "rank10RaidPairs": len(raids),
        "liveGoldPairs": len(gold),
        "frontier": str(FRONTIER),
        "frontierSha256": sha256_file(FRONTIER),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
