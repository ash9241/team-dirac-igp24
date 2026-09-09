#!/usr/bin/env python3
"""Run Cross-100 pair-sibling shard B and stage only current priority hits."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import sqlite3
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
SHARD = DATA / "agent_pair_sibling_exact_frontier_shard2.jsonl"
WORKER = ROOT / "agent_gold_a_recovered_pair_sibling_worker.py"
RANK10 = Path("/private/tmp/rank10_raid/low_hanging_fruit_unique_placements.jsonl")
TEAM_ID = "teamv2_07f0f7f581c34fd1a0dd913dad90dcec"
RESULTS = DATA / "agent_pair_sibling_shard2_results.jsonl"
HITS = DATA / "agent_pair_sibling_shard2_priority_hits.jsonl"
MANIFEST = ROOT / "outbox" / "agent_pair_sibling_shard2_live_priority.txt"
SUMMARY = DATA / "agent_pair_sibling_shard2_summary.json"


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


def write_lines_atomic(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(f"{line}\n" for line in lines), encoding="ascii")
    temporary.replace(path)


def write_json_atomic(path: Path, row: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def rank10_evidence() -> dict[tuple[str, int], dict]:
    result = {}
    for row in read_jsonl(RANK10):
        if str(row.get("teamId")) == TEAM_ID and int(row.get("kTeams", 0)) == 1:
            result[(str(row["label"]), int(row["r"]))] = row
    return result


def run_one(task: dict, timeout: int) -> dict:
    command = [
        "python3",
        str(WORKER),
        str(task["submissionId"]),
        str(task["polynomialIndex"]),
        "--transforms",
        "1,2,3,5,7",
        "--nfdisc",
        "--timeout",
        str(timeout),
    ]
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout + 15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            **task,
            "status": "outer_timeout",
            "workerWallSeconds": round(time.time() - started, 3),
            "workerCommand": command,
        }
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return {
            **task,
            "status": "worker_error",
            "workerExitCode": completed.returncode,
            "workerStderrTail": completed.stderr[-2000:],
            "workerWallSeconds": round(time.time() - started, 3),
            "workerCommand": command,
        }
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return {
            **task,
            "status": "invalid_worker_output",
            "error": str(exc),
            "workerExitCode": completed.returncode,
            "workerStdoutTail": completed.stdout[-2000:],
            "workerStderrTail": completed.stderr[-2000:],
            "workerWallSeconds": round(time.time() - started, 3),
            "workerCommand": command,
        }
    return {
        **task,
        **result,
        "workerExitCode": completed.returncode,
        "workerStderrTail": completed.stderr[-2000:],
        "workerWallSeconds": round(time.time() - started, 3),
        "workerCommand": command,
    }


def current_priority_audit(result: dict, evidence: dict[tuple[str, int], dict]) -> dict:
    if result.get("status") != "certified":
        return {"priorityHit": False, "reason": "not_certified"}
    pair = (str(result["targetLabel"]), int(result["targetR"]))
    digest = str(result["coefficientSha256"])
    line = str(result["coefficientLine"])
    if hashlib.sha256(line.encode("ascii")).hexdigest() != digest:
        return {"priorityHit": False, "reason": "coefficient_hash_mismatch"}
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        hash_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
            ).fetchone()[0]
        )
        owned_rows = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM verifications
                WHERE label=? AND r=? AND scoreable=1
                """,
                pair,
            ).fetchone()[0]
        )
        target = connection.execute(
            "SELECT team_count,minimum_disc_abs FROM targets WHERE label=? AND r=?",
            pair,
        ).fetchone()
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
        ).fetchone()
    base = {
        "priorityHit": False,
        "targetLabel": pair[0],
        "targetR": pair[1],
        "ledgerCoefficientRows": hash_rows,
        "unseenCoefficientHash": hash_rows == 0,
        "ownedExactTargetPairRows": owned_rows,
        "unownedExactTargetPair": owned_rows == 0,
        "baselinePair": baseline is not None,
        "targetTeamCount": int(target[0]) if target is not None else None,
    }
    eligible = hash_rows == 0 and owned_rows == 0 and baseline is None and target is not None
    if eligible and int(target[0]) == 0:
        return {
            **base,
            "priorityHit": True,
            "priorityKind": "live_gold",
            "projectedCandidateScore": 1.0,
            "projectedNetRelativeSwing": 1.0,
        }
    holder = evidence.get(pair)
    field_disc = result.get("fieldDiscriminantAbs")
    if eligible and int(target[0]) == 1 and holder is not None and field_disc is not None:
        holder_disc = int(holder.get("minScoringDiscAbs") or holder["scoringDiscAbs"])
        candidate_disc = int(field_disc)
        if holder_disc > 1 and candidate_disc > 1:
            ratio = min(1.0, math.log(holder_disc) / math.log(candidate_disc))
            candidate_score = 0.5 * ratio
            return {
                **base,
                "priorityHit": True,
                "priorityKind": "rank10_solo_raid",
                "rank10HolderTeamId": TEAM_ID,
                "holderScoringDiscAbs": str(holder_disc),
                "candidateFieldDiscAbs": str(candidate_disc),
                "discRatio": ratio,
                "projectedCandidateScore": candidate_score,
                "projectedNetRelativeSwing": 0.5 + candidate_score,
                "beatsHolderDiscriminant": candidate_disc < holder_disc,
            }
    return {**base, "reason": "not_current_gold_or_rank10_solo"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--continue-after-hit", action="store_true")
    parser.add_argument("--shard", type=Path, default=SHARD)
    parser.add_argument("--rank-start", type=int, default=101)
    parser.add_argument("--rank-end", type=int, default=200)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--hits", type=Path, default=HITS)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument(
        "--summarize-existing",
        action="store_true",
        help="finalize an already completed checkpoint without rerunning workers",
    )
    args = parser.parse_args()
    if args.workers != 6:
        parser.error("this reserved shard must run with exactly six workers")

    tasks = read_jsonl(args.shard)
    expected_rows = args.rank_end - args.rank_start + 1
    if expected_rows < 1 or len(tasks) != expected_rows:
        raise RuntimeError(
            f"reserved shard has {len(tasks)} rows, expected {expected_rows}"
        )
    ranks = [int(row["stableExactGateRank"]) for row in tasks]
    if ranks != list(range(args.rank_start, args.rank_end + 1)):
        raise RuntimeError(
            f"reserved shard ranks are not exactly {args.rank_start}..{args.rank_end}"
        )

    if args.summarize_existing:
        results = read_jsonl(args.results)
        hits = read_jsonl(args.hits)
        manifest_lines = (
            args.manifest.read_text(encoding="ascii").splitlines()
            if args.manifest.exists()
            else []
        )
        if len(results) != len(tasks):
            raise RuntimeError(
                f"existing checkpoint has {len(results)} results, expected {len(tasks)}"
            )
        launched_unix = min(
            float(row["completedUnix"]) - float(row.get("workerWallSeconds", 0.0))
            for row in results
        )
        summary = {
            "launchedUnix": launched_unix,
            "finishedUnix": max(float(row["completedUnix"]) for row in results),
            "workers": args.workers,
            "shardPath": relative_path(args.shard),
            "shardSha256": sha256_file(args.shard),
            "reservedRanks": [args.rank_start, args.rank_end],
            "attemptsLaunched": len(results),
            "attemptsCompleted": len(results),
            "certified": sum(row.get("status") == "certified" for row in results),
            "failed": sum(row.get("status") != "certified" for row in results),
            "priorityHits": len(hits),
            "goldHits": sum(
                row["postCertificationPriorityAudit"].get("priorityKind") == "live_gold"
                for row in hits
            ),
            "rank10RaidHits": sum(
                row["postCertificationPriorityAudit"].get("priorityKind")
                == "rank10_solo_raid"
                for row in hits
            ),
            "stoppedOnFirstHit": bool(hits) and not args.continue_after_hit,
            "resultsPath": relative_path(args.results),
            "hitsPath": relative_path(args.hits),
            "manifestPath": relative_path(args.manifest),
            "manifestRows": len(manifest_lines),
        }
        write_json_atomic(args.summary, summary)
        summary["resultsSha256"] = sha256_file(args.results)
        summary["hitsSha256"] = sha256_file(args.hits)
        summary["manifestSha256"] = sha256_file(args.manifest)
        print(json.dumps({"event": "summary", **summary}, sort_keys=True), flush=True)
        return 0

    launched_unix = time.time()
    evidence = rank10_evidence()
    results: list[dict] = []
    hits: list[dict] = []
    manifest_lines: list[str] = []
    next_index = 0
    stop_launching = False

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    active: dict[concurrent.futures.Future, dict] = {}
    try:
        while next_index < len(tasks) and len(active) < args.workers:
            task = tasks[next_index]
            active[pool.submit(run_one, task, args.timeout)] = task
            next_index += 1
        while active:
            done, _pending = concurrent.futures.wait(
                active, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                task = active.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # retain the shard checkpoint
                    result = {**task, "status": "runner_exception", "error": repr(exc)}
                audit = current_priority_audit(result, evidence)
                result["postCertificationPriorityAudit"] = audit
                result["completedUnix"] = time.time()
                results.append(result)
                results.sort(key=lambda row: int(row["stableExactGateRank"]))
                if audit.get("priorityHit"):
                    hits.append(result)
                    digest = str(result["coefficientSha256"])
                    if digest not in {
                        hashlib.sha256(line.encode("ascii")).hexdigest()
                        for line in manifest_lines
                    }:
                        manifest_lines.append(str(result["coefficientLine"]))
                    if not args.continue_after_hit:
                        stop_launching = True
                write_jsonl_atomic(args.results, results)
                write_jsonl_atomic(
                    args.hits,
                    sorted(hits, key=lambda row: int(row["stableExactGateRank"])),
                )
                write_lines_atomic(args.manifest, manifest_lines)
                print(
                    json.dumps(
                        {
                            "event": "completed",
                            "stableRank": int(result["stableExactGateRank"]),
                            "completed": len(results),
                            "status": result.get("status"),
                            "targetLabel": result.get("targetLabel"),
                            "targetR": result.get("targetR"),
                            "priorityHit": bool(audit.get("priorityHit")),
                            "priorityKind": audit.get("priorityKind"),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            while (
                not stop_launching
                and next_index < len(tasks)
                and len(active) < args.workers
            ):
                task = tasks[next_index]
                active[pool.submit(run_one, task, args.timeout)] = task
                next_index += 1
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    summary = {
        "launchedUnix": launched_unix,
        "finishedUnix": time.time(),
        "workers": args.workers,
        "shardPath": relative_path(args.shard),
        "shardSha256": sha256_file(args.shard),
        "reservedRanks": [args.rank_start, args.rank_end],
        "attemptsLaunched": next_index,
        "attemptsCompleted": len(results),
        "certified": sum(row.get("status") == "certified" for row in results),
        "failed": sum(row.get("status") != "certified" for row in results),
        "priorityHits": len(hits),
        "goldHits": sum(
            row["postCertificationPriorityAudit"].get("priorityKind") == "live_gold"
            for row in hits
        ),
        "rank10RaidHits": sum(
            row["postCertificationPriorityAudit"].get("priorityKind")
            == "rank10_solo_raid"
            for row in hits
        ),
        "stoppedOnFirstHit": bool(hits) and not args.continue_after_hit,
        "resultsPath": relative_path(args.results),
        "hitsPath": relative_path(args.hits),
        "manifestPath": relative_path(args.manifest),
        "manifestRows": len(manifest_lines),
    }
    write_json_atomic(args.summary, summary)
    summary["resultsSha256"] = sha256_file(args.results)
    summary["hitsSha256"] = sha256_file(args.hits)
    summary["manifestSha256"] = sha256_file(args.manifest)
    print(json.dumps({"event": "summary", **summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
