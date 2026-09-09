#!/usr/bin/env python3
"""Run the compact disjoint Cross-1500 stage-1 extension with six workers.

The runner reuses the locked bank's exact output auditor and current-value
checks.  It dynamically launches work so a first exact certificate stops new
launches; otherwise every currently valuable extension command is exhausted.
There are no network calls or submission operations.  An exact live hit is
only written to an agent-local outbox manifest after the shared pair claim is
acquired.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_BANK = DATA / "agent_gold_b_cross1500_extension_bank.jsonl"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_RESULTS = DATA / "agent_gold_b_cross1500_extension_results.jsonl"
DEFAULT_HITS = DATA / "agent_gold_b_cross1500_extension_hits.jsonl"
DEFAULT_SUMMARY = DATA / "agent_gold_b_cross1500_extension_run_summary.json"
DEFAULT_MANIFEST = ROOT / "outbox" / "agent_gold_b_cross1500_extension_hits.txt"
DEFAULT_CLAIMS = DATA / "agent_cross1500_stage1_pair_claims"
DEFAULT_RANK10 = Path(
    "/private/tmp/rank10_raid/low_hanging_fruit_unique_placements.jsonl"
)


def load_locked_runner():
    path = ROOT / "agent_gold_a_run_cross1500_stage1_odd.py"
    spec = importlib.util.spec_from_file_location("cross1500_locked_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import locked runner from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOCKED = load_locked_runner()


def append_manifest(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def compact_manifest_path(path: Path, maximum_lines: int) -> Path:
    """Return a manifest shard with room for one more coefficient line."""
    candidate = path
    part = 1
    while candidate.exists():
        with candidate.open("r", encoding="utf-8") as handle:
            used = sum(bool(line.strip()) for line in handle)
        if used < maximum_lines:
            return candidate
        part += 1
        candidate = path.with_name(f"{path.stem}_part{part}{path.suffix}")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--hits", type=Path, default=DEFAULT_HITS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--claims", type=Path, default=DEFAULT_CLAIMS)
    parser.add_argument("--rank10-evidence", type=Path, default=DEFAULT_RANK10)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--continue-after-hit",
        action="store_true",
        help="exhaust the bank instead of stopping new launches at the first hit",
    )
    parser.add_argument(
        "--best-per-pair",
        action="store_true",
        help=(
            "defer claims until bank completion and stage only the minimum-"
            "discriminant exact candidate for each distinct live target pair"
        ),
    )
    parser.add_argument("--maximum-manifest-lines", type=int, default=20)
    parser.add_argument("--expected-bank-sha256", required=True)
    args = parser.parse_args()
    if not 1 <= args.workers <= 6:
        raise ValueError("the extension shard must use between one and six workers")
    if args.maximum_manifest_lines < 1:
        raise ValueError("maximum manifest lines must be positive")

    bank_hash = LOCKED.sha256_path(args.bank)
    if bank_hash != args.expected_bank_sha256:
        raise ValueError(
            f"extension bank hash mismatch: {bank_hash} != {args.expected_bank_sha256}"
        )
    all_tasks = LOCKED.load_bank(args.bank)
    if not all_tasks:
        raise ValueError("extension bank has no commands")
    for task in all_tasks:
        output_path = Path(task["output"])
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
    command_ids = [
        str(task["command"][-1]) + "|" + str(task["output"]) for task in all_tasks
    ]
    if len(command_ids) != len(set(command_ids)):
        raise ValueError("extension bank contains duplicate command/output identities")

    rank10_solos = LOCKED.load_rank10_solos(args.rank10_evidence)
    pairs = sorted({(task["targetLabel"], task["targetR"]) for task in all_tasks})
    prechecks = {
        pair: LOCKED.current_live_value(
            args.db, pair[0], pair[1], "", rank10_solos
        )
        for pair in pairs
    }
    selected = [
        task
        for task in all_tasks
        if prechecks[(task["targetLabel"], task["targetR"])]["stageable"]
    ]
    for task in selected:
        precheck = prechecks[(task["targetLabel"], task["targetR"])]
        if precheck["teamCount"] == 1 and "--max-team-count" not in task["command"]:
            task["command"] = task["command"] + ["--max-team-count", "1"]

    completed = LOCKED.load_completed(args.results)
    pending = [
        task for task in selected if task["globalCommandIndex"] not in completed
    ]
    known_hashes = LOCKED.staged_hashes()
    started = time.monotonic()
    completed_this_run = 0
    exact_hits = sum(
        bool(row.get("exactCertified")) for row in completed.values()
    )
    staged_hits = sum(bool(row.get("staged")) for row in completed.values())
    failures = sum(bool(row.get("workerFailure")) for row in completed.values())
    first_hit = exact_hits > 0
    keep_launching = args.continue_after_hit or args.best_per_pair
    best_candidates: dict[tuple[str, int], dict] = {}
    next_pending = 0

    print(
        json.dumps(
            {
                "bankSha256": bank_hash,
                "commands": len(all_tasks),
                "currentValuable": len(selected),
                "event": "start",
                "pending": len(pending),
                "resumed": len(completed),
                "skippedNonvaluable": len(all_tasks) - len(selected),
                "workers": args.workers,
            },
            sort_keys=True,
        ),
        flush=True,
    )

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    active: dict[concurrent.futures.Future, dict] = {}
    try:
        while (
            next_pending < len(pending)
            and len(active) < args.workers
            and (keep_launching or not first_hit)
        ):
            task = pending[next_pending]
            next_pending += 1
            active[pool.submit(LOCKED.execute, task, args.timeout)] = task

        while active:
            done, _unused = concurrent.futures.wait(
                active, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                task = active.pop(future)
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        **{key: value for key, value in task.items() if key != "command"},
                        "auditErrors": [
                            f"runner exception: {type(exc).__name__}: {exc}"
                        ],
                        "certifiedCandidate": None,
                        "elapsedSeconds": None,
                        "outputParseStatus": "runner_exception",
                        "returnCode": None,
                        "timedOut": False,
                    }

                candidate = row.pop("certifiedCandidate", None)
                row["exactCertified"] = candidate is not None and not row.get(
                    "auditErrors"
                )
                row["staged"] = False
                row["workerFailure"] = (
                    row.get("timedOut") is True
                    or row.get("outputParseStatus")
                    in {"missing", "invalid", "runner_exception"}
                    or bool(row.get("auditErrors"))
                    or row.get("returnCode") not in {None, 0, 2}
                )
                failures += int(row["workerFailure"])

                if row["exactCertified"]:
                    exact_hits += 1
                    candidate_hash = str(candidate["candidateSha256"])
                    live = LOCKED.current_live_value(
                        args.db,
                        task["targetLabel"],
                        task["targetR"],
                        candidate_hash,
                        rank10_solos,
                    )
                    row["candidateSha256"] = candidate_hash
                    row["candidateFieldDiscriminantAbs"] = str(
                        candidate["fieldDiscriminantAbs"]
                    )
                    row["currentLiveTarget"] = live
                    row["stagedHashDuplicate"] = candidate_hash in known_hashes
                    claim_row = {
                        "candidateCoefficientLine": candidate[
                            "candidateCoefficientLine"
                        ],
                        "candidateFieldDiscriminantAbs": row[
                            "candidateFieldDiscriminantAbs"
                        ],
                        "candidateSha256": candidate_hash,
                        "globalCommandIndex": task["globalCommandIndex"],
                        "logicalTaskId": task["logicalTaskId"],
                        "output": row["output"],
                        "owner": "agent_gold_b_cross1500_extension",
                        "targetLabel": task["targetLabel"],
                        "targetR": task["targetR"],
                    }
                    if (
                        args.best_per_pair
                        and live["stageable"]
                        and not row["stagedHashDuplicate"]
                    ):
                        pair = (task["targetLabel"], task["targetR"])
                        claim_path = (
                            args.claims
                            / f"{task['targetLabel']}_r{task['targetR']}.json"
                        )
                        row["pairClaim"] = str(claim_path.resolve())
                        row["pairClaimedByThisShard"] = False
                        row["bestPerPairDeferred"] = not claim_path.exists()
                        if not claim_path.exists():
                            key = (int(candidate["fieldDiscriminantAbs"]), candidate_hash)
                            prior_best = best_candidates.get(pair)
                            if prior_best is None or key < prior_best["key"]:
                                best_candidates[pair] = {
                                    "candidate": candidate,
                                    "claimRow": claim_row,
                                    "key": key,
                                    "live": live,
                                }
                                print(
                                    json.dumps(
                                        {
                                            "candidateFieldDiscriminantAbs": str(
                                                candidate["fieldDiscriminantAbs"]
                                            ),
                                            "candidateSha256": candidate_hash,
                                            "event": "distinct_exact_candidate",
                                            "pair": f"{pair[0]}/r{pair[1]}",
                                        },
                                        sort_keys=True,
                                    ),
                                    flush=True,
                                )
                    elif live["stageable"] and not row["stagedHashDuplicate"]:
                        claimed, claim_path = LOCKED.atomic_pair_claim(
                            args.claims, claim_row
                        )
                        row["pairClaim"] = claim_path
                        row["pairClaimedByThisShard"] = claimed
                        if claimed:
                            append_manifest(
                                args.manifest, candidate["candidateCoefficientLine"]
                            )
                            known_hashes.add(candidate_hash)
                            row["staged"] = True
                            row["manifest"] = str(args.manifest.resolve())
                            staged_hits += 1
                            LOCKED.append_jsonl(
                                args.hits,
                                {**claim_row, "currentLiveTarget": live},
                            )
                    if not first_hit:
                        first_hit = True
                        print(
                            json.dumps(
                                {
                                    "candidateSha256": candidate_hash,
                                    "currentLiveTarget": live,
                                    "event": "first_exact_hit",
                                    "globalCommandIndex": task[
                                        "globalCommandIndex"
                                    ],
                                    "logicalTaskId": task["logicalTaskId"],
                                    "staged": row["staged"],
                                },
                                sort_keys=True,
                            ),
                            flush=True,
                        )

                LOCKED.append_jsonl(args.results, row)
                completed[int(task["globalCommandIndex"])] = row
                completed_this_run += 1
                print(
                    json.dumps(
                        {
                            "completedThisRun": completed_this_run,
                            "elapsedSeconds": round(time.monotonic() - started, 3),
                            "event": "completed",
                            "exactCertified": row["exactCertified"],
                            "globalCommandIndex": task["globalCommandIndex"],
                            "logicalTaskId": task["logicalTaskId"],
                            "staged": row["staged"],
                            "workerFailure": row["workerFailure"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

            while (
                next_pending < len(pending)
                and len(active) < args.workers
                and (keep_launching or not first_hit)
            ):
                task = pending[next_pending]
                next_pending += 1
                active[pool.submit(LOCKED.execute, task, args.timeout)] = task
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    if args.best_per_pair:
        for pair, best in sorted(best_candidates.items()):
            candidate = best["candidate"]
            claim_row = best["claimRow"]
            candidate_hash = str(candidate["candidateSha256"])
            live = LOCKED.current_live_value(
                args.db, pair[0], pair[1], candidate_hash, rank10_solos
            )
            if not live["stageable"] or candidate_hash in known_hashes:
                continue
            claimed, claim_path = LOCKED.atomic_pair_claim(args.claims, claim_row)
            if not claimed:
                continue
            manifest_path = compact_manifest_path(
                args.manifest, args.maximum_manifest_lines
            )
            append_manifest(manifest_path, candidate["candidateCoefficientLine"])
            known_hashes.add(candidate_hash)
            staged_hits += 1
            LOCKED.append_jsonl(
                args.hits,
                {
                    **claim_row,
                    "currentLiveTarget": live,
                    "manifest": str(manifest_path.resolve()),
                    "pairClaim": claim_path,
                },
            )
            print(
                json.dumps(
                    {
                        "candidateFieldDiscriminantAbs": str(
                            candidate["fieldDiscriminantAbs"]
                        ),
                        "candidateSha256": candidate_hash,
                        "event": "distinct_exact_hit",
                        "manifest": str(manifest_path.resolve()),
                        "pair": f"{pair[0]}/r{pair[1]}",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    unlaunched_after_hit = len(pending) - next_pending
    summary = {
        "bank": str(args.bank.resolve()),
        "bankSha256": bank_hash,
        "commands": len(all_tasks),
        "completedThisRun": completed_this_run,
        "completedTotal": len(completed),
        "currentValuable": len(selected),
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "exactCertified": exact_hits,
        "failed": failures,
        "hits": str(args.hits.resolve()),
        "launchedThisRun": next_pending,
        "manifest": str(args.manifest.resolve()),
        "networkCalls": 0,
        "pendingAtStart": len(pending),
        "results": str(args.results.resolve()),
        "resumed": len(completed) - completed_this_run,
        "selected": len(selected),
        "skippedNonvaluable": len(all_tasks) - len(selected),
        "staged": staged_hits,
        "status": (
            "exhaustive_complete"
            if keep_launching or not first_hit
            else "stopped_after_first_exact_hit"
        ),
        "submissionCalls": 0,
        "unlaunchedAfterFirstHit": unlaunched_after_hit,
        "workers": args.workers,
    }
    LOCKED.write_summary(args.summary, summary)
    print(json.dumps({"event": "complete", **summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
