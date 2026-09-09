#!/usr/bin/env python3
"""Run deterministic even Cross-1500 character commands and stage exact hits."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BANK = DATA / "agent_gold_a_cross1500_character_bank.jsonl"
DB = DATA / "ledger.sqlite3"
RANK10 = Path("/private/tmp/rank10_raid/low_hanging_fruit_unique_placements.jsonl")
TEAM_ID = "teamv2_07f0f7f581c34fd1a0dd913dad90dcec"
CLAIMS = DATA / "agent_cross1500_stage1_pair_claims"
ATTEMPTS = DATA / "agent_gold_b_cross1500_stage1_even_attempts.jsonl"
HITS = DATA / "agent_gold_b_cross1500_stage1_even_hits.jsonl"
MANIFEST = ROOT / "outbox" / "agent_gold_b_cross1500_stage1_even_priority.txt"
SUMMARY = DATA / "agent_gold_b_cross1500_stage1_even_summary.json"


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


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def flatten_even() -> list[dict]:
    flat = []
    for row_index, row in enumerate(read_jsonl(BANK)):
        if row.get("status") != "executable":
            continue
        for attempt_index, attempt in enumerate(row["attempts"]):
            global_index = len(flat)
            flat.append(
                {
                    "globalCommandIndex": global_index,
                    "bankRowIndex": row_index,
                    "attemptIndexWithinTask": attempt_index,
                    "logicalTaskId": str(row["logicalTaskId"]),
                    "target": row["target"],
                    "attempt": attempt,
                }
            )
    if len(flat) != 630:
        raise RuntimeError(f"locked bank flattened to {len(flat)} commands, expected 630")
    even = [row for row in flat if int(row["globalCommandIndex"]) % 2 == 0]
    if len(even) != 315:
        raise RuntimeError(f"even shard has {len(even)} commands, expected 315")
    return even


def load_rank10() -> dict[tuple[str, int], dict]:
    return {
        (str(row["label"]), int(row["r"])): row
        for row in read_jsonl(RANK10)
        if str(row.get("teamId")) == TEAM_ID and int(row.get("kTeams", 0)) == 1
    }


RANK10_EVIDENCE = load_rank10()


def current_pair(pair: tuple[str, int]) -> dict:
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        target = connection.execute(
            "SELECT team_count,minimum_disc_abs FROM targets WHERE label=? AND r=?",
            pair,
        ).fetchone()
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
        ).fetchone()
        owned = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM verifications
                WHERE label=? AND r=? AND scoreable=1
                """,
                pair,
            ).fetchone()[0]
        )
    team_count = int(target[0]) if target is not None else None
    result = {
        "label": pair[0],
        "r": pair[1],
        "teamCount": team_count,
        "minimumDiscAbs": str(target[1]) if target is not None and target[1] else None,
        "baseline": baseline is not None,
        "ownedRows": owned,
        "locallyOwned": owned > 0,
        "priorityKind": None,
        "currentPriority": False,
    }
    if target is None or baseline is not None or owned > 0:
        return result
    if team_count == 0:
        result.update({"priorityKind": "live_gold", "currentPriority": True})
    elif team_count == 1 and pair in RANK10_EVIDENCE:
        result.update({"priorityKind": "rank10_solo_raid", "currentPriority": True})
    return result


def run_command(item: dict, timeout: int) -> dict:
    pair = (str(item["target"]["label"]), int(item["target"]["r"]))
    precheck = current_pair(pair)
    base = {
        **{key: value for key, value in item.items() if key != "attempt"},
        "outputPath": str(item["attempt"]["output"]),
        "auxiliaryPrimes": item["attempt"].get("auxiliaryPrimes", []),
        "precheck": precheck,
    }
    if not precheck["currentPriority"]:
        return {
            **base,
            "status": "precheck_outside_current_priority",
            "commandExecuted": False,
            "workerWallSeconds": 0.0,
        }

    output = Path(item["attempt"]["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(value) for value in item["attempt"]["command"]]
    if precheck["priorityKind"] == "rank10_solo_raid" and "--max-team-count" not in command:
        command.extend(["--max-team-count", "1"])
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            **base,
            "status": "worker_timeout",
            "commandExecuted": True,
            "executedCommand": command,
            "workerWallSeconds": round(time.time() - started, 3),
        }
    result = {
        **base,
        "status": "worker_completed" if output.exists() else "worker_error_no_output",
        "commandExecuted": True,
        "executedCommand": command,
        "workerExitCode": completed.returncode,
        "workerStdoutTail": completed.stdout[-1200:],
        "workerStderrTail": completed.stderr[-2000:],
        "workerWallSeconds": round(time.time() - started, 3),
    }
    if output.exists():
        result["outputSha256"] = sha256_file(output)
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
            result["searchStatus"] = payload.get("search", {}).get("status")
            result["certifiedCandidateCount"] = sum(
                str(row.get("status", "")).startswith("certified_")
                for row in payload.get("search", {}).get("candidateResults", [])
            )
        except (OSError, json.JSONDecodeError) as exc:
            result["status"] = "invalid_result_file"
            result["resultFileError"] = str(exc)
    return result


def certified_candidates(result: dict) -> list[dict]:
    output = Path(result["outputPath"])
    if not output.exists():
        return []
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    expected_label = str(result["target"]["label"])
    expected_r = int(result["target"]["r"])
    expected_status = f"certified_{expected_label}_r{expected_r}"
    certified = []
    for row in payload.get("search", {}).get("candidateResults", []):
        line = str(row.get("candidateCoefficientLine", ""))
        digest = hashlib.sha256(line.encode("ascii")).hexdigest() if line else None
        exact = bool(
            row.get("status") == expected_status
            and row.get("irreducible") is True
            and int(row.get("realRoots", -1)) == expected_r
            and row.get("maximalSubgroupCertificate", {}).get("complete") is True
            and row.get("containmentProof", {}).get("sameDegree12Field") is True
            and digest == row.get("candidateSha256")
            and row.get("fieldDiscriminantAbs") is not None
        )
        if exact:
            certified.append(row)
    return certified


def candidate_audit(result: dict, candidate: dict) -> dict:
    pair = (str(result["target"]["label"]), int(result["target"]["r"]))
    current = current_pair(pair)
    digest = str(candidate["candidateSha256"])
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        hash_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
            ).fetchone()[0]
        )
    audit = {
        **current,
        "candidateSha256": digest,
        "ledgerCoefficientRows": hash_rows,
        "unseenCoefficientHash": hash_rows == 0,
        "exactLocalCertificate": True,
        "stageablePriorityHit": current["currentPriority"] and hash_rows == 0,
    }
    if current["priorityKind"] == "live_gold":
        audit.update({"projectedCandidateScore": 1.0, "projectedNetRelativeSwing": 1.0})
    elif current["priorityKind"] == "rank10_solo_raid":
        holder = RANK10_EVIDENCE[pair]
        holder_disc = int(holder.get("minScoringDiscAbs") or holder["scoringDiscAbs"])
        candidate_disc = int(candidate["fieldDiscriminantAbs"])
        ratio = min(1.0, math.log(holder_disc) / math.log(candidate_disc))
        audit.update(
            {
                "holderScoringDiscAbs": str(holder_disc),
                "candidateFieldDiscAbs": str(candidate_disc),
                "discRatio": ratio,
                "projectedCandidateScore": 0.5 * ratio,
                "projectedNetRelativeSwing": 0.5 + 0.5 * ratio,
                "beatsHolderDiscriminant": candidate_disc < holder_disc,
            }
        )
    return audit


def claim_pair(result: dict, candidate: dict, audit: dict) -> tuple[bool, Path, dict | None]:
    CLAIMS.mkdir(parents=True, exist_ok=True)
    pair = (str(result["target"]["label"]), int(result["target"]["r"]))
    path = CLAIMS / f"{pair[0]}_r{pair[1]}.json"
    claim = {
        "claimedAtUnix": time.time(),
        "owner": "even",
        "globalCommandIndex": int(result["globalCommandIndex"]),
        "logicalTaskId": str(result["logicalTaskId"]),
        "label": pair[0],
        "r": pair[1],
        "candidateSha256": str(candidate["candidateSha256"]),
        "priorityKind": audit["priorityKind"],
        "outputPath": str(result["outputPath"]),
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        return False, path, existing
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(claim, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
    return True, path, claim


def main() -> int:
    workers = 6
    timeout = 900
    assigned = flatten_even()
    prior = read_jsonl(ATTEMPTS)
    results_by_index = {int(row["globalCommandIndex"]): row for row in prior}
    hits = read_jsonl(HITS)
    manifest_lines = (
        MANIFEST.read_text(encoding="ascii").splitlines() if MANIFEST.exists() else []
    )
    pending = [row for row in assigned if int(row["globalCommandIndex"]) not in results_by_index]
    launched_unix = time.time()
    next_index = 0
    first_hit: dict | None = None

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    active: dict[concurrent.futures.Future, dict] = {}
    try:
        while next_index < len(pending) and len(active) < workers:
            item = pending[next_index]
            active[pool.submit(run_command, item, timeout)] = item
            next_index += 1
        while active:
            done, _ = concurrent.futures.wait(
                active, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                item = active.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        **{key: value for key, value in item.items() if key != "attempt"},
                        "outputPath": str(item["attempt"]["output"]),
                        "status": "runner_exception",
                        "error": repr(exc),
                    }
                result["completedUnix"] = time.time()
                result_hits = []
                for candidate in certified_candidates(result):
                    audit = candidate_audit(result, candidate)
                    hit = {
                        **{key: value for key, value in result.items() if key not in ("workerStdoutTail", "workerStderrTail")},
                        "candidate": candidate,
                        "postCertificateAudit": audit,
                    }
                    if audit["stageablePriorityHit"]:
                        claimed, claim_path, claim = claim_pair(result, candidate, audit)
                        hit["pairClaimCreated"] = claimed
                        hit["pairClaimPath"] = str(claim_path.relative_to(ROOT))
                        hit["pairClaim"] = claim
                        if claimed:
                            line = str(candidate["candidateCoefficientLine"])
                            if line not in manifest_lines:
                                manifest_lines.append(line)
                            hit["staged"] = True
                        else:
                            hit["staged"] = False
                            hit["stageReason"] = "pair_claim_already_exists"
                        result_hits.append(hit)
                result["priorityHitCount"] = len(result_hits)
                results_by_index[int(result["globalCommandIndex"])] = result
                hits.extend(result_hits)
                if result_hits and first_hit is None:
                    first_hit = result_hits[0]
                ordered_results = [results_by_index[index] for index in sorted(results_by_index)]
                hits.sort(key=lambda row: int(row["globalCommandIndex"]))
                write_jsonl_atomic(ATTEMPTS, ordered_results)
                write_jsonl_atomic(HITS, hits)
                write_lines_atomic(MANIFEST, manifest_lines)
                print(
                    json.dumps(
                        {
                            "event": "completed",
                            "globalCommandIndex": int(result["globalCommandIndex"]),
                            "completed": len(ordered_results),
                            "status": result.get("status"),
                            "workerExitCode": result.get("workerExitCode"),
                            "workerWallSeconds": result.get("workerWallSeconds"),
                            "priorityHits": len(result_hits),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            while next_index < len(pending) and len(active) < workers:
                item = pending[next_index]
                active[pool.submit(run_command, item, timeout)] = item
                next_index += 1
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    ordered_results = [results_by_index[index] for index in sorted(results_by_index)]
    summary = {
        "bankPath": str(BANK.relative_to(ROOT)),
        "bankSha256": sha256_file(BANK),
        "parity": "even_global_indices_0_based",
        "assignedCommands": len(assigned),
        "launchedThisRun": next_index,
        "completedTotal": len(ordered_results),
        "commandExecuted": sum(bool(row.get("commandExecuted")) for row in ordered_results),
        "precheckSkipped": sum(
            row.get("status") == "precheck_outside_current_priority"
            for row in ordered_results
        ),
        "resultFiles": sum(Path(row["outputPath"]).exists() for row in ordered_results),
        "certifiedAttempts": sum(int(row.get("certifiedCandidateCount", 0)) > 0 for row in ordered_results),
        "priorityHits": len(hits),
        "stagedClaims": sum(bool(row.get("pairClaimCreated")) for row in hits),
        "manifestRows": len(manifest_lines),
        "firstHitObserved": first_hit is not None,
        "stoppedOnFirstHit": False,
        "launchedUnix": launched_unix,
        "finishedUnix": time.time(),
        "attemptsPath": str(ATTEMPTS.relative_to(ROOT)),
        "hitsPath": str(HITS.relative_to(ROOT)),
        "manifestPath": str(MANIFEST.relative_to(ROOT)),
    }
    write_json_atomic(SUMMARY, summary)
    summary["attemptsSha256"] = sha256_file(ATTEMPTS)
    summary["hitsSha256"] = sha256_file(HITS)
    summary["manifestSha256"] = sha256_file(MANIFEST)
    print(json.dumps({"event": "summary", **summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
