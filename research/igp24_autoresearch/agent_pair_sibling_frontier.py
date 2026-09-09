#!/usr/bin/env python3
"""Build a deterministic recovered pair-sibling Cross-100 frontier.

The source census is frozen at the ledger state observed before the current
submission backfill.  Signature profiles are exact GAP computations.  Exact
rank-10 solo raids are ranked first, exact live golds second, and the unchanged
stable source census fills any remaining rows.  This preserves a 200-row
disjoint work allocation even when fewer than 200 exact valuable routes exist.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ORBIT_MAP = DATA / "pair_orbit_map.jsonl"
PRODUCTION_RESULTS = DATA / "pair_sum_candidates.jsonl"
EXPANDED_RESULTS = DATA / "pair_sum_single_expanded_candidates.jsonl"
PROFILE_INPUTS = (
    DATA / "pair_signature_map.jsonl",
    DATA / "rank12_route_signature_profiles.jsonl",
)
PROFILE_CACHE = DATA / "agent_pair_sibling_shard2_profiles.jsonl"
FRONTIER = DATA / "agent_gold_a_cross100_forced_frontier.jsonl"
SHARD = DATA / "agent_pair_sibling_exact_frontier_shard2.jsonl"
SUMMARY = DATA / "agent_gold_a_cross100_forced_frontier_summary.json"
PROFILE_WORKER = ROOT / "pair_signature_one.sage.py"
RANK10_PLACEMENTS = Path(
    "/private/tmp/rank10_raid/low_hanging_fruit_unique_placements.jsonl"
)
RANK10_TEAM_ID = "teamv2_07f0f7f581c34fd1a0dd913dad90dcec"
RANK10_TEAM_NUMBER = "IGP24-T00110"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_pair(row: dict) -> tuple[str, int]:
    return str(row["sourceLabel"]), int(row["sourceR"])


def source_key(row: dict) -> tuple[str, int]:
    return (
        str(row.get("sourceSubmissionId", row.get("submissionId"))),
        int(row.get("sourcePolynomialIndex", row.get("polynomialIndex"))),
    )


def load_unique_nonself_orbits() -> dict[str, dict]:
    result = {}
    for row in read_jsonl(ORBIT_MAP):
        targets = list(row.get("targets") or [])
        if len(targets) != 1:
            continue
        if str(targets[0]["targetLabel"]) == str(row["sourceLabel"]):
            continue
        result[str(row["sourceLabel"])] = row
    return result


def snapshot_tasks(cutoff: float) -> tuple[list[dict], dict, dict[str, dict]]:
    orbits = load_unique_nonself_orbits()
    historical = [*read_jsonl(PRODUCTION_RESULTS), *read_jsonl(EXPANDED_RESULTS)]
    tested_pairs = {
        source_pair(row)
        for row in historical
        if row.get("sourceLabel") is not None and row.get("sourceR") is not None
    }
    tested_keys = {
        source_key(row)
        for row in historical
        if row.get("sourceSubmissionId", row.get("submissionId")) is not None
        and row.get("sourcePolynomialIndex", row.get("polynomialIndex")) is not None
    }

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        owned_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT DISTINCT v.label,v.r
                FROM verifications AS v
                JOIN submissions AS s USING(submission_id)
                WHERE v.scoreable=1 AND s.synced_at <= ?
                """,
                (cutoff,),
            )
        }
        baseline_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        target_values: dict[str, list[tuple[int, int]]] = {}
        for label, r, team_count in connection.execute(
            "SELECT label,r,team_count FROM targets"
        ):
            pair = (str(label), int(r))
            if pair in owned_pairs or pair in baseline_pairs:
                continue
            target_values.setdefault(str(label), []).append(
                (int(r), int(team_count))
            )

        rows = connection.execute(
            """
            WITH ranked_sources AS (
                SELECT
                    v.submission_id,
                    v.polynomial_index,
                    v.label,
                    v.t,
                    v.r,
                    length(p.original_line) AS coefficient_bytes,
                    ROW_NUMBER() OVER (
                        PARTITION BY v.label,v.r
                        ORDER BY v.submission_id,v.polynomial_index
                    ) AS source_rank
                FROM verifications AS v
                JOIN polynomials AS p USING(submission_id,polynomial_index)
                JOIN submissions AS s USING(submission_id)
                WHERE v.scoreable=1 AND s.synced_at <= ?
            )
            SELECT * FROM ranked_sources
            WHERE source_rank=1
            ORDER BY label,r,submission_id,polynomial_index
            """,
            (cutoff,),
        ).fetchall()
    finally:
        connection.close()

    tasks = []
    skips: dict[str, int] = {}

    def skip(reason: str) -> None:
        skips[reason] = skips.get(reason, 0) + 1

    for row in rows:
        label = str(row["label"])
        source_r = int(row["r"])
        orbit = orbits.get(label)
        if orbit is None:
            skip("no_nonself_single_orbit")
            continue
        if (label, source_r) in tested_pairs:
            skip("source_pair_already_tested")
            continue
        key = (str(row["submission_id"]), int(row["polynomial_index"]))
        if key in tested_keys:
            skip("source_key_already_tested")
            continue
        target_label = str(orbit["targets"][0]["targetLabel"])
        available = target_values.get(target_label, [])
        if not available:
            skip("target_label_no_snapshot_available_pair")
            continue
        minimum_team_count = min(team_count for _r, team_count in available)
        tasks.append(
            {
                "submissionId": key[0],
                "polynomialIndex": key[1],
                "sourceLabel": label,
                "sourceT": int(row["t"]),
                "sourceR": source_r,
                "sourceCoefficientBytes": int(row["coefficient_bytes"]),
                "targetLabel": target_label,
                "targetMinTeamCount": minimum_team_count,
                "targetAvailableSignatureCount": len(available),
            }
        )
    tasks.sort(
        key=lambda row: (
            int(row["targetMinTeamCount"]),
            -int(row["targetAvailableSignatureCount"]),
            int(row["targetLabel"][3:]),
            int(row["sourceLabel"][3:]),
            int(row["sourceR"]),
        )
    )
    audit = {
        "cutoffEpoch": cutoff,
        "mappedEligibleSingleSourceLabels": len(orbits),
        "testedSourcePairs": len(tested_pairs),
        "testedSourceKeys": len(tested_keys),
        "eligibleBeforeGate": len(tasks),
        "skipCounts": dict(sorted(skips.items())),
    }
    return tasks, audit, orbits


def live_gold_pairs() -> set[tuple[str, int]]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        return {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT t.label,t.r
                FROM targets AS t
                LEFT JOIN baseline_pairs AS b
                  ON b.label=t.label AND b.r=t.r
                LEFT JOIN (
                    SELECT DISTINCT label,r
                    FROM verifications
                    WHERE scoreable=1
                ) AS owned
                  ON owned.label=t.label AND owned.r=t.r
                WHERE t.team_count=0
                  AND b.label IS NULL
                  AND owned.label IS NULL
                """
            )
        }
    finally:
        connection.close()


def priority_pairs() -> tuple[dict[tuple[str, int], dict], set[tuple[str, int]]]:
    """Freeze current rank-10 solo raids and live golds."""
    evidence: dict[tuple[str, int], dict] = {}
    for row in read_jsonl(RANK10_PLACEMENTS):
        if str(row.get("teamId")) != RANK10_TEAM_ID or int(row.get("kTeams", 0)) != 1:
            continue
        evidence[(str(row["label"]), int(row["r"]))] = row

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        baseline = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        team_counts = {
            (str(row[0]), int(row[1])): int(row[2])
            for row in connection.execute("SELECT label,r,team_count FROM targets")
        }
    finally:
        connection.close()
    raids = {
        pair: row
        for pair, row in evidence.items()
        if pair not in baseline and pair not in owned and team_counts.get(pair) == 1
    }
    return raids, live_gold_pairs()


def load_profiles() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in (*PROFILE_INPUTS, PROFILE_CACHE):
        for row in read_jsonl(path):
            if row.get("status") not in (None, "certified"):
                continue
            result[str(row["sourceLabel"])] = row
    return result


def identity_profile(orbit: dict) -> dict:
    target = orbit["targets"][0]
    return {
        "sourceLabel": str(orbit["sourceLabel"]),
        "sourceT": int(orbit["sourceT"]),
        "length24OrbitCount": 1,
        "profiles": [
            {
                "classIndex": -1,
                "classSize": 1,
                "order": 1,
                "sourceR": 24,
                "orbitSignatures": [
                    {
                        "orbitIndex": int(target["orbitIndex"]),
                        "targetLabel": str(target["targetLabel"]),
                        "targetR": 24,
                    }
                ],
            }
        ],
        "status": "certified",
    }


def compute_profile(label: str, source_t: int, timeout: int) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            ["sage", "-python", str(PROFILE_WORKER), label, str(source_t)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "sourceLabel": label,
            "sourceT": source_t,
            "status": "timeout",
            "profileWallSeconds": round(time.monotonic() - started, 3),
        }
    if completed.returncode != 0:
        return {
            "sourceLabel": label,
            "sourceT": source_t,
            "status": "error",
            "stderrTail": completed.stderr[-1200:],
            "profileWallSeconds": round(time.monotonic() - started, 3),
        }
    try:
        row = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            "sourceLabel": label,
            "sourceT": source_t,
            "status": "invalid_output",
            "error": str(exc),
            "profileWallSeconds": round(time.monotonic() - started, 3),
        }
    row["status"] = "certified"
    row["profileWallSeconds"] = round(time.monotonic() - started, 3)
    return row


def forced_route(
    task: dict,
    orbit: dict,
    profile: dict,
    raids: dict[tuple[str, int], dict],
    gold: set[tuple[str, int]],
    original_rank: int,
) -> dict | None:
    compatible = [
        row
        for row in profile.get("profiles", [])
        if int(row["sourceR"]) == int(task["sourceR"])
    ]
    if not compatible:
        return None
    realized_pairs: set[tuple[str, int]] = set()
    for class_profile in compatible:
        signatures = list(class_profile.get("orbitSignatures") or [])
        if len(signatures) != 1:
            return None
        signature = signatures[0]
        pair = (str(signature["targetLabel"]), int(signature["targetR"]))
        if pair[0] != str(task["targetLabel"]):
            return None
        realized_pairs.add(pair)
    # The exact pair must be invariant across every compatible class.  Merely
    # forcing the target label is insufficient for either a raid or a gold.
    if len(realized_pairs) != 1:
        return None
    pair = next(iter(realized_pairs))
    if pair in raids:
        tier = 0
        kind = "rank10_solo_raid"
        holder = raids[pair]
        holder_disc = str(
            holder.get("minScoringDiscAbs") or holder.get("scoringDiscAbs")
        )
        scoring = {
            "rank10HolderTeamId": RANK10_TEAM_ID,
            "rank10HolderTeamNumber": RANK10_TEAM_NUMBER,
            "holderScoringDiscAbs": holder_disc,
            "candidateDiscRatioFormula": "min(1,log(D_holder)/log(D_candidate))",
            "candidateScoreFormula": "0.5*discRatio",
            "netRelativeSwingFormula": "0.5+candidateScore",
        }
    elif pair in gold:
        tier = 1
        kind = "live_gold"
        scoring = {"projectedCandidateScore": 1.0, "projectedNetRelativeSwing": 1.0}
    else:
        return None
    return {
        **task,
        "sourceSnapshotRank": original_rank,
        "priorityTier": tier,
        "priorityKind": kind,
        "compatibleClassIndexes": [int(row["classIndex"]) for row in compatible],
        "compatibleClassCount": len(compatible),
        "forcedExactPairAcrossCompatibleClasses": True,
        "forcedTargetLabel": pair[0],
        "forcedTargetR": pair[1],
        "scoringPriority": scoring,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", type=float, default=1784628497.513)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--profile-timeout", type=int, default=180)
    parser.add_argument("--need", type=int, default=200)
    args = parser.parse_args()
    if args.workers < 1 or args.need < 200:
        parser.error("workers must be positive and need must be at least 200")

    started_unix = time.time()
    tasks, audit, orbits = snapshot_tasks(args.cutoff)
    print(json.dumps({"event": "snapshot", **audit}), flush=True)
    if audit["eligibleBeforeGate"] != 5365:
        raise RuntimeError(
            "snapshot reconstruction drift: expected 5365 tasks, got "
            f"{audit['eligibleBeforeGate']}"
        )

    # Freeze the priority predicate once so stable ranks are reproducible.
    priority_snapshot_unix = time.time()
    raids, gold = priority_pairs()
    priority_labels = {pair[0] for pair in raids} | {pair[0] for pair in gold}
    relevant_tasks = [task for task in tasks if task["targetLabel"] in priority_labels]
    profiles = load_profiles()
    cached_rows = {
        str(row["sourceLabel"]): row for row in read_jsonl(PROFILE_CACHE)
    }
    computed_this_run = 0
    failed_profiles: list[dict] = []
    missing: dict[str, int] = {}
    for task in relevant_tasks:
        label = str(task["sourceLabel"])
        if int(task["sourceR"]) != 24 and label not in profiles:
            missing[label] = int(task["sourceT"])
    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(compute_profile, label, source_t, args.profile_timeout): label
                for label, source_t in missing.items()
            }
            for future in concurrent.futures.as_completed(futures):
                row = future.result()
                label = futures[future]
                if row.get("status") == "certified":
                    profiles[label] = row
                    cached_rows[label] = row
                    computed_this_run += 1
                else:
                    failed_profiles.append(row)
        write_jsonl_atomic(
            PROFILE_CACHE,
            [cached_rows[label] for label in sorted(cached_rows, key=lambda x: int(x[3:]))],
        )

    exact_routes: list[dict] = []
    for original_rank, task in enumerate(tasks, start=1):
        if task not in relevant_tasks:
            continue
        orbit = orbits[str(task["sourceLabel"])]
        profile = (
            identity_profile(orbit)
            if int(task["sourceR"]) == 24
            else profiles.get(str(task["sourceLabel"]))
        )
        if profile is None:
            continue
        route = forced_route(task, orbit, profile, raids, gold, original_rank)
        if route is not None:
            exact_routes.append(route)
    exact_routes.sort(key=lambda row: (int(row["priorityTier"]), int(row["sourceSnapshotRank"])))

    # One stable representative per exact target pair, as agreed for Cross-100.
    deduplicated: list[dict] = []
    seen_pairs: set[tuple[str, int]] = set()
    for route in exact_routes:
        pair = (str(route["forcedTargetLabel"]), int(route["forcedTargetR"]))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        deduplicated.append(route)

    chosen_sources = {source_key(row) for row in deduplicated}
    fallback = []
    for original_rank, task in enumerate(tasks, start=1):
        if source_key(task) in chosen_sources:
            continue
        fallback.append(
            {
                **task,
                "sourceSnapshotRank": original_rank,
                "priorityTier": 2,
                "priorityKind": "stable_open_label_fallback",
                "forcedExactPairAcrossCompatibleClasses": False,
            }
        )
    ordered = [*deduplicated, *fallback]
    selected_frontier = []
    for stable_rank, route in enumerate(ordered[: args.need], start=1):
        selected_frontier.append(
            {
                **route,
                "stableExactGateRank": stable_rank,
                "sourceSnapshotCutoffEpoch": args.cutoff,
                "prioritySnapshotUnix": priority_snapshot_unix,
            }
        )
    shard = selected_frontier[100:200]
    if len(shard) != 100:
        raise RuntimeError(f"second shard has {len(shard)} rows, expected 100")
    write_jsonl_atomic(FRONTIER, selected_frontier)
    write_jsonl_atomic(SHARD, shard)
    summary = {
        **audit,
        "startedUnix": started_unix,
        "prioritySnapshotUnix": priority_snapshot_unix,
        "rank10EvidencePath": str(RANK10_PLACEMENTS),
        "rank10EvidenceTeamId": RANK10_TEAM_ID,
        "rank10SoloPairCount": len(raids),
        "liveGoldPairCount": len(gold),
        "priorityLabelRelevantTasks": len(relevant_tasks),
        "exactForcedRoutesBeforePairDedup": len(exact_routes),
        "exactForcedRoutesAfterPairDedup": len(deduplicated),
        "rank10RaidRoutes": sum(row["priorityTier"] == 0 for row in deduplicated),
        "liveGoldRoutes": sum(row["priorityTier"] == 1 for row in deduplicated),
        "stableFallbackRowsInFrontier": sum(
            row["priorityTier"] == 2 for row in selected_frontier
        ),
        "frontierRows": len(selected_frontier),
        "shardStartStableRank": 101,
        "shardEndStableRank": 200,
        "shardRows": len(shard),
        "profilesComputedThisRun": computed_this_run,
        "profileFailures": failed_profiles,
        "frontierPath": str(FRONTIER.relative_to(ROOT)),
        "shardPath": str(SHARD.relative_to(ROOT)),
    }
    write_json_atomic(SUMMARY, summary)
    summary["frontierSha256"] = sha256_file(FRONTIER)
    summary["shardSha256"] = sha256_file(SHARD)
    summary["summaryPath"] = str(SUMMARY.relative_to(ROOT))
    print(json.dumps({"event": "complete", **summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
