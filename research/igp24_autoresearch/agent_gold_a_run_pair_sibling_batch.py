#!/usr/bin/env python3
"""Run and safely stage one deterministic exact pair-sibling frontier shard."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
WORKER = ROOT / "pair_sum_one.sage.py"
RECOVERY_WORKER = ROOT / "agent_gold_a_recovered_pair_sibling_worker.py"
DEFAULT_FRONTIER = DATA / "agent_gold_a_cross100_forced_frontier.jsonl"
DEFAULT_RESULTS = DATA / "agent_gold_a_cross100_batch_results.jsonl"
DEFAULT_HITS = DATA / "agent_gold_a_cross100_batch_hits.jsonl"
DEFAULT_SUMMARY = DATA / "agent_gold_a_cross100_batch_summary.json"
DEFAULT_MANIFEST = ROOT / "outbox" / "agent_gold_a_cross100_batch_staged.txt"
KNOWN_HITS = DATA / "agent_gold_a_cross100_forced_hits.jsonl"
RANK10_EVIDENCE = Path(
    "/private/tmp/rank10_raid/low_hanging_fruit_unique_placements.jsonl"
)
RANK10_TEAM_ID = "teamv2_07f0f7f581c34fd1a0dd913dad90dcec"


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def write_manifest_atomic(path: Path, hits: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    seen = set()
    for row in hits:
        digest = str(row["coefficientSha256"])
        if digest in seen:
            continue
        seen.add(digest)
        lines.append(str(row["coefficientLine"]) + "\n")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    temporary.replace(path)


def source_key(row: dict) -> tuple[str, int]:
    return str(row["submissionId"]), int(row["polynomialIndex"])


def run_route(task: dict, timeout: int) -> dict:
    command = [
        "sage",
        "-python",
        str(WORKER),
        str(task["submissionId"]),
        str(task["polynomialIndex"]),
        "--expected-target",
        str(task["targetLabel"]),
        "--transforms",
        "1,2,3,5,7",
        "--reduce",
        "best",
    ]
    started = time.monotonic()
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
        return {**task, "status": "timeout", "workerWallSeconds": timeout}
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            **task,
            "status": "invalid_output",
            "error": str(exc),
            "stderrTail": completed.stderr[-1200:],
            "workerWallSeconds": round(time.monotonic() - started, 3),
        }
    return {
        **task,
        **result,
        "workerExitCode": completed.returncode,
        "workerWallSeconds": round(time.monotonic() - started, 3),
    }


def canonical_hash(line: str) -> str:
    values = [int(field.strip()) for field in line.split(",")]
    if len(values) != 25:
        raise ValueError("candidate does not have 25 coefficients")
    canonical = ",".join(str(value) for value in values)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def outbox_hashes(exclude: Path) -> set[str]:
    result = set()
    for path in (ROOT / "outbox").glob("*.txt"):
        if path.resolve() == exclude.resolve():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                result.add(canonical_hash(line.split("#", 1)[0].strip()))
            except (ValueError, TypeError):
                continue
    return result


def rank10_pairs(
    evidence_path: Path = RANK10_EVIDENCE,
) -> dict[tuple[str, int], dict]:
    result = {}
    for row in read_jsonl(evidence_path):
        if str(row.get("teamId")) != RANK10_TEAM_ID:
            continue
        if int(row.get("kTeams", 0)) != 1:
            continue
        result[(str(row["label"]), int(row["r"]))] = row
    return result


def audit_candidate(
    result: dict,
    rank10: dict[tuple[str, int], dict],
    staged_hashes: set[str],
    staged_pairs: set[tuple[str, int]],
) -> dict:
    line = str(result["coefficientLine"])
    digest = canonical_hash(line)
    if digest != str(result["coefficientSha256"]):
        raise ArithmeticError("candidate coefficient hash mismatch")
    pair = (str(result["targetLabel"]), int(result["targetR"]))
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        ledger_hash_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
            ).fetchone()[0]
        )
        owned_pair_rows = int(
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
    finally:
        connection.close()
    team_count = int(target[0]) if target is not None else None
    kind = None
    holder = rank10.get(pair)
    if team_count == 0:
        kind = "live_gold"
    elif team_count == 1 and holder is not None:
        kind = "rank10_solo_raid"
    fresh = bool(
        kind
        and baseline is None
        and owned_pair_rows == 0
        and ledger_hash_rows == 0
        and digest not in staged_hashes
        and pair not in staged_pairs
    )
    return {
        "targetPair": {"label": pair[0], "r": pair[1]},
        "targetTeamCount": team_count,
        "targetMinimumDiscAbs": str(target[1]) if target and target[1] else None,
        "baseline": baseline is not None,
        "ledgerCoefficientRows": ledger_hash_rows,
        "ownedExactTargetPairRows": owned_pair_rows,
        "alreadyInOutbox": digest in staged_hashes,
        "alreadyStagedTargetPair": pair in staged_pairs,
        "strategicKind": kind,
        "rank10Holder": holder,
        "stageable": fresh,
    }


def exact_nfdisc(task: dict, timeout: int) -> dict:
    command = [
        sys.executable,
        str(RECOVERY_WORKER),
        str(task["submissionId"]),
        str(task["polynomialIndex"]),
        "--transforms",
        "1,2,3,5,7",
        "--reduce",
        "best",
        "--nfdisc",
        "--timeout",
        str(timeout),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout + 10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"nfdisc rerun failed for {source_key(task)}: {completed.stderr[-1200:]}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def score_hit(result: dict, audit: dict, nfdisc_result: dict) -> dict:
    if str(nfdisc_result["coefficientSha256"]) != str(result["coefficientSha256"]):
        raise ArithmeticError("nfdisc rerun changed the candidate hash")
    candidate_disc = int(nfdisc_result["fieldDiscriminantAbs"])
    scoring = {
        "candidateFieldDiscAbs": str(candidate_disc),
        "candidateFieldDiscLog": math.log(candidate_disc),
    }
    if audit["strategicKind"] == "live_gold":
        scoring.update(
            {"discRatio": 1.0, "candidateScore": 1.0, "netRelativeSwing": 1.0}
        )
    else:
        holder = audit["rank10Holder"]
        holder_disc = int(
            holder.get("minScoringDiscAbs") or holder.get("scoringDiscAbs")
        )
        ratio = min(1.0, math.log(holder_disc) / math.log(candidate_disc))
        candidate_score = 0.5 * ratio
        scoring.update(
            {
                "holderFieldDiscAbs": str(holder_disc),
                "holderFieldDiscLog": math.log(holder_disc),
                "discRatio": ratio,
                "candidateScore": candidate_score,
                "netRelativeSwing": 0.5 + candidate_score,
                "formula": "ratio=min(1,log(D_holder)/log(D_candidate)); score=.5*ratio; swing=.5+score",
            }
        )
    return {
        **result,
        "status": "certified_staged",
        "stageAudit": audit,
        "scoring": scoring,
        "fieldDiscriminantAbs": str(candidate_disc),
        "nfdiscCertificate": {
            "resolventSha256": nfdisc_result["resolventSha256"],
            "orbitCertificate": nfdisc_result["orbitCertificate"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontier", type=Path, default=DEFAULT_FRONTIER)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--hits", type=Path, default=DEFAULT_HITS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--rank10-evidence",
        type=Path,
        default=RANK10_EVIDENCE,
        help="frozen sole-holder placement snapshot used to authorize raids",
    )
    args = parser.parse_args()
    if args.offset < 0 or args.limit < 1 or args.workers < 1 or args.timeout < 1:
        parser.error("offset must be nonnegative; limit/workers/timeout must be positive")

    frontier = read_jsonl(args.frontier)[args.offset : args.offset + args.limit]
    if len(frontier) != args.limit:
        parser.error(f"requested {args.limit} rows but frontier slice has {len(frontier)}")
    results = read_jsonl(args.results)
    result_keys = {source_key(row) for row in results}
    hits = read_jsonl(args.hits)
    known_hits = read_jsonl(KNOWN_HITS)
    staged_hashes = outbox_hashes(args.manifest) | {
        str(row["coefficientSha256"]) for row in [*known_hits, *hits]
    }
    staged_pairs = {
        (str(row["targetLabel"]), int(row["targetR"]))
        for row in [*known_hits, *hits]
    }
    rank10 = rank10_pairs(args.rank10_evidence)
    pending = [row for row in frontier if source_key(row) not in result_keys]
    started = time.time()
    first_hit = None

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_route, task, args.timeout): task for task in pending}
        for completed_index, future in enumerate(
            concurrent.futures.as_completed(futures), start=1
        ):
            row = future.result()
            results.append(row)
            write_jsonl_atomic(args.results, results)
            hit = None
            if row.get("status") == "certified":
                audit = audit_candidate(row, rank10, staged_hashes, staged_pairs)
                row["stageAudit"] = audit
                write_jsonl_atomic(args.results, results)
                if audit["stageable"]:
                    nfdisc_result = exact_nfdisc(row, args.timeout)
                    # Re-audit immediately after the expensive nfdisc calculation.
                    audit = audit_candidate(row, rank10, staged_hashes, staged_pairs)
                    if audit["stageable"]:
                        hit = score_hit(row, audit, nfdisc_result)
                        hits.append(hit)
                        staged_hashes.add(str(hit["coefficientSha256"]))
                        staged_pairs.add((str(hit["targetLabel"]), int(hit["targetR"])))
                        write_jsonl_atomic(args.hits, hits)
                        write_manifest_atomic(args.manifest, hits)
                        if first_hit is None:
                            first_hit = hit
            summary = {
                "frontier": str(args.frontier),
                "offset": args.offset,
                "limit": args.limit,
                "workers": args.workers,
                "resumedResults": len(results) - completed_index,
                "completedThisRun": completed_index,
                "totalCompleted": len(results),
                "certified": sum(row.get("status") == "certified" for row in results),
                "failed": sum(row.get("status") != "certified" for row in results),
                "stagedHits": len(hits),
                "liveGoldHits": sum(
                    row["stageAudit"]["strategicKind"] == "live_gold" for row in hits
                ),
                "rank10RaidHits": sum(
                    row["stageAudit"]["strategicKind"] == "rank10_solo_raid"
                    for row in hits
                ),
                "elapsedSeconds": round(time.time() - started, 3),
                "results": str(args.results),
                "hits": str(args.hits),
                "manifest": str(args.manifest),
            }
            write_json_atomic(args.summary, summary)
            event = {
                "event": "completed",
                "completedThisRun": completed_index,
                "stableRank": row.get("stableExactGateRank"),
                "sourceLabel": row.get("sourceLabel"),
                "sourceR": row.get("sourceR"),
                "targetLabel": row.get("targetLabel"),
                "targetR": row.get("targetR"),
                "status": row.get("status"),
                "hit": (
                    {
                        "kind": hit["stageAudit"]["strategicKind"],
                        "hash": hit["coefficientSha256"],
                        "netRelativeSwing": hit["scoring"]["netRelativeSwing"],
                    }
                    if hit is not None
                    else None
                ),
            }
            print(json.dumps(event, sort_keys=True), flush=True)

    print(json.dumps({"event": "complete", **summary}, sort_keys=True), flush=True)
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
