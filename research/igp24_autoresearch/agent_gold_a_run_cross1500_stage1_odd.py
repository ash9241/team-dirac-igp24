#!/usr/bin/env python3
"""Run the odd-index half of the locked Cross-1500 character bank.

The global command index is obtained by reading the JSONL bank in file order
and flattening each row's ``attempts`` array in array order.  This runner owns
only odd zero-based indices.  Each underlying Sage job performs the exact
character alignment, degree/sign checks, and maximal-subgroup Frobenius
certificate.  A result is staged only after those certificates are audited
again here and the pair is still live, unowned, nonbaseline gold in the
current local ledger.

There are no network or submission operations in this runner.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_BANK = ROOT / "data" / "agent_gold_a_cross1500_character_bank.jsonl"
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_RESULTS = ROOT / "data" / "agent_gold_a_cross1500_stage1_odd_results.jsonl"
DEFAULT_HITS = ROOT / "data" / "agent_gold_a_cross1500_stage1_odd_hits.jsonl"
DEFAULT_SUMMARY = ROOT / "data" / "agent_gold_a_cross1500_stage1_odd_summary.json"
DEFAULT_MANIFEST = ROOT / "outbox" / "agent_gold_a_cross1500_stage1_odd_staged.txt"
DEFAULT_CLAIMS = ROOT / "data" / "agent_cross1500_stage1_pair_claims"
DEFAULT_RANK10_EVIDENCE = Path(
    "/private/tmp/rank10_raid/low_hanging_fruit_unique_placements.jsonl"
)
LOCKED_BANK_SHA256 = "f79a962846feacd3d6dca9c7d86ee4237a3af041b8c74ab48f074f6d268a52ef"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_bank(path: Path) -> list[dict]:
    tasks = []
    global_index = 0
    with path.open("r", encoding="utf-8") as handle:
        for row_index, line in enumerate(handle):
            if not line.strip():
                continue
            row = json.loads(line)
            for attempt_index, attempt in enumerate(row.get("attempts", [])):
                tasks.append(
                    {
                        "attemptIndex": attempt_index,
                        "command": [str(value) for value in attempt["command"]],
                        "globalCommandIndex": global_index,
                        "logicalTaskId": str(row["logicalTaskId"]),
                        "output": str(attempt["output"]),
                        "rowIndex": row_index,
                        "targetLabel": str(row["target"]["label"]),
                        "targetR": int(row["target"]["r"]),
                    }
                )
                global_index += 1
    return tasks


def load_completed(path: Path) -> dict[int, dict]:
    completed = {}
    if not path.exists():
        return completed
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            completed[int(row["globalCommandIndex"])] = row
    return completed


def load_rank10_solos(path: Path) -> set[tuple[str, int]]:
    """Load the frozen sole-holder pairs attributed to IGP24-T00110."""
    pairs = set()
    if not path.exists():
        return pairs
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                row.get("teamNumber") == "IGP24-T00110"
                and int(row.get("kTeams", 0)) == 1
                and row.get("label") is not None
                and row.get("r") is not None
            ):
                pairs.add((str(row["label"]), int(row["r"])))
    return pairs


def parse_and_audit_output(task: dict, output_path: Path) -> dict:
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "auditErrors": [f"unreadable output: {type(exc).__name__}: {exc}"],
            "certifiedCandidate": None,
            "outputParseStatus": "invalid",
        }

    audit = payload.get("audit", {})
    search = payload.get("search", {})
    certified = [
        row
        for row in search.get("candidateResults", [])
        if str(row.get("status", "")).startswith("certified_")
    ]
    if not certified:
        return {
            "auditErrors": [],
            "certifiedCandidate": None,
            "outputParseStatus": "valid_no_certificate",
            "searchStatus": search.get("status"),
        }
    errors = []
    if len(certified) != 1:
        errors.append(f"expected exactly one certified candidate, found {len(certified)}")
    candidate = certified[0]
    expected_status = f"certified_{task['targetLabel']}_r{task['targetR']}"
    if candidate.get("status") != expected_status:
        errors.append(
            f"certificate status {candidate.get('status')!r} != {expected_status!r}"
        )
    line = str(candidate.get("candidateCoefficientLine", ""))
    digest = sha256_bytes(line.encode())
    if not line or digest != candidate.get("candidateSha256"):
        errors.append("candidate coefficient hash mismatch")
    certificate = candidate.get("maximalSubgroupCertificate", {})
    if certificate.get("complete") is not True:
        errors.append("maximal-subgroup certificate is incomplete")
    containment = candidate.get("containmentProof", {})
    if containment.get("sameDegree12Field") is not True:
        errors.append("same-degree-12-field containment proof is absent")
    if candidate.get("irreducible") is not True:
        errors.append("candidate is not exactly marked irreducible")
    if int(candidate.get("realRoots", -1)) != task["targetR"]:
        errors.append("candidate real-root count does not equal requested r")
    if not candidate.get("fieldDiscriminantAbs"):
        errors.append("exact field discriminant is absent")
    live_snapshot = audit.get("liveTarget", {})
    if live_snapshot.get("pair") != f"{task['targetLabel']}/r{task['targetR']}":
        errors.append("worker live-target pair does not match task")
    structure = audit.get("targetStructure", {})
    if structure.get("targetLabel") != task["targetLabel"]:
        errors.append("worker target structure does not match task")
    if audit.get("networkCalls") != 0 or audit.get("submissionCalls") != 0:
        errors.append("worker audit does not certify zero network/submission calls")
    return {
        "auditErrors": errors,
        "certifiedCandidate": candidate,
        "outputParseStatus": "valid_certificate" if not errors else "invalid_certificate",
        "searchStatus": search.get("status"),
    }


def execute(task: dict, timeout_seconds: int) -> dict:
    output_path = Path(task["output"])
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    started = time.monotonic()
    reused = output_path.exists()
    stdout = ""
    stderr = ""
    return_code = None
    timed_out = False
    if not reused:
        try:
            completed = subprocess.run(
                task["command"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
            return_code = int(completed.returncode)
            stdout = completed.stdout[-4000:]
            stderr = completed.stderr[-4000:]
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else ""
    parsed = parse_and_audit_output(task, output_path) if output_path.exists() else {
        "auditErrors": ["worker produced no output file"],
        "certifiedCandidate": None,
        "outputParseStatus": "missing",
    }
    return {
        **{key: value for key, value in task.items() if key != "command"},
        **parsed,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "output": str(output_path.resolve()),
        "returnCode": return_code,
        "reusedExistingOutput": reused,
        "stderrTail": stderr,
        "stdoutTail": stdout,
        "timedOut": timed_out,
    }


def current_live_value(
    db: Path,
    label: str,
    r: int,
    candidate_hash: str,
    rank10_solos: set[tuple[str, int]],
) -> dict:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT team_count,minimum_disc_abs,generated_at "
            "FROM targets WHERE label=? AND r=?",
            (label, r),
        ).fetchone()
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1",
            (label, r),
        ).fetchone() is not None
        locally_owned = connection.execute(
            "SELECT 1 FROM verifications "
            "WHERE label=? AND r=? AND scoreable=1 LIMIT 1",
            (label, r),
        ).fetchone() is not None
        in_ledger = connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (candidate_hash,),
        ).fetchone() is not None
    finally:
        connection.close()
    rank10_solo = (label, r) in rank10_solos
    if row is None:
        return {
            "baseline": None,
            "candidateAlreadyInLedger": in_ledger,
            "generatedAt": None,
            "locallyOwned": None,
            "minimumDiscAbs": None,
            "pair": f"{label}/r{r}",
            "rank10SoloEvidence": rank10_solo,
            "stageable": False,
            "teamCount": None,
        }
    result = {
        "baseline": baseline,
        "candidateAlreadyInLedger": in_ledger,
        "generatedAt": str(row[2]) if row[2] else None,
        "locallyOwned": locally_owned,
        "minimumDiscAbs": str(row[1]) if row[1] else None,
        "pair": f"{label}/r{r}",
        "rank10SoloEvidence": rank10_solo,
        "teamCount": int(row[0]),
    }
    result["stageable"] = (
        (result["teamCount"] == 0 or (result["teamCount"] == 1 and rank10_solo))
        and not result["baseline"]
        and not result["locallyOwned"]
        and not result["candidateAlreadyInLedger"]
    )
    return result


def staged_hashes() -> set[str]:
    hashes = set()
    outbox = ROOT / "outbox"
    if not outbox.exists():
        return hashes
    for path in outbox.glob("*.txt"):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        hashes.add(sha256_bytes(line.encode()))
        except (OSError, UnicodeError):
            continue
    return hashes


def atomic_pair_claim(claim_dir: Path, row: dict) -> tuple[bool, str]:
    claim_dir.mkdir(parents=True, exist_ok=True)
    claim_path = claim_dir / f"{row['targetLabel']}_r{row['targetR']}.json"
    rendered = json.dumps(row, indent=2, sort_keys=True) + "\n"
    try:
        descriptor = os.open(
            claim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
    except FileExistsError:
        return False, str(claim_path.resolve())
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    return True, str(claim_path.resolve())


def write_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--hits", type=Path, default=DEFAULT_HITS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--claims", type=Path, default=DEFAULT_CLAIMS)
    parser.add_argument(
        "--rank10-evidence", type=Path, default=DEFAULT_RANK10_EVIDENCE
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--expected-bank-sha256", default=LOCKED_BANK_SHA256)
    args = parser.parse_args()

    bank_hash = sha256_path(args.bank)
    if bank_hash != args.expected_bank_sha256:
        raise ValueError(
            f"locked bank hash mismatch: {bank_hash} != {args.expected_bank_sha256}"
        )
    all_tasks = load_bank(args.bank)
    if len(all_tasks) != 630:
        raise ValueError(f"locked bank must flatten to 630 commands, found {len(all_tasks)}")
    assigned = [task for task in all_tasks if task["globalCommandIndex"] % 2 == 1]
    if len(assigned) != 315:
        raise ValueError(f"odd shard must contain 315 commands, found {len(assigned)}")
    rank10_solos = load_rank10_solos(args.rank10_evidence)
    assigned_pairs = sorted(
        {(task["targetLabel"], task["targetR"]) for task in assigned}
    )
    precheck_by_pair = {
        pair: current_live_value(
            args.db,
            pair[0],
            pair[1],
            "",
            rank10_solos,
        )
        for pair in assigned_pairs
    }
    selected = [
        task
        for task in assigned
        if precheck_by_pair[(task["targetLabel"], task["targetR"])]["stageable"]
    ]
    for task in selected:
        precheck = precheck_by_pair[(task["targetLabel"], task["targetR"])]
        if precheck["teamCount"] == 1:
            task["command"] = task["command"] + ["--max-team-count", "1"]
    completed = load_completed(args.results)
    pending = [
        task for task in selected if task["globalCommandIndex"] not in completed
    ]
    known_hashes = staged_hashes()
    started = time.monotonic()
    completed_this_run = 0
    exact_hits = sum(
        1 for row in completed.values() if row.get("exactCertified") is True
    )
    staged_hits = sum(1 for row in completed.values() if row.get("staged") is True)
    failures = sum(1 for row in completed.values() if row.get("workerFailure") is True)
    first_hit_reported = exact_hits > 0

    print(
        json.dumps(
            {
                "bankSha256": bank_hash,
                "event": "start",
                "globalIndexParity": "odd",
                "pending": len(pending),
                "resumed": len(completed),
                "assigned": len(assigned),
                "currentValuable": len(selected),
                "skippedNonvaluable": len(assigned) - len(selected),
                "workers": args.workers,
            },
            sort_keys=True,
        ),
        flush=True,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_task = {
            pool.submit(execute, task, args.timeout): task for task in pending
        }
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    **{key: value for key, value in task.items() if key != "command"},
                    "auditErrors": [f"runner exception: {type(exc).__name__}: {exc}"],
                    "certifiedCandidate": None,
                    "elapsedSeconds": None,
                    "outputParseStatus": "runner_exception",
                    "returnCode": None,
                    "timedOut": False,
                }

            candidate = row.pop("certifiedCandidate", None)
            row["exactCertified"] = candidate is not None and not row.get("auditErrors")
            row["staged"] = False
            row["workerFailure"] = (
                row.get("timedOut") is True
                or row.get("outputParseStatus") in {"missing", "invalid", "runner_exception"}
                or bool(row.get("auditErrors"))
                or row.get("returnCode") not in {None, 0, 2}
            )
            if row["workerFailure"]:
                failures += 1

            if row["exactCertified"]:
                exact_hits += 1
                candidate_hash = str(candidate["candidateSha256"])
                live = current_live_value(
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
                    "candidateCoefficientLine": candidate["candidateCoefficientLine"],
                    "candidateFieldDiscriminantAbs": row[
                        "candidateFieldDiscriminantAbs"
                    ],
                    "candidateSha256": candidate_hash,
                    "globalCommandIndex": task["globalCommandIndex"],
                    "logicalTaskId": task["logicalTaskId"],
                    "output": row["output"],
                    "owner": "agent_gold_a_stage1_odd",
                    "targetLabel": task["targetLabel"],
                    "targetR": task["targetR"],
                }
                if live["stageable"] and not row["stagedHashDuplicate"]:
                    claimed, claim_path = atomic_pair_claim(args.claims, claim_row)
                    row["pairClaim"] = claim_path
                    row["pairClaimedByThisShard"] = claimed
                    if claimed:
                        args.manifest.parent.mkdir(parents=True, exist_ok=True)
                        with args.manifest.open("a", encoding="utf-8") as handle:
                            handle.write(candidate["candidateCoefficientLine"] + "\n")
                            handle.flush()
                            os.fsync(handle.fileno())
                        known_hashes.add(candidate_hash)
                        row["staged"] = True
                        row["manifest"] = str(args.manifest.resolve())
                        staged_hits += 1
                        append_jsonl(args.hits, {**claim_row, "currentLiveTarget": live})
                if not first_hit_reported:
                    first_hit_reported = True
                    print(
                        json.dumps(
                            {
                                "candidateSha256": candidate_hash,
                                "currentLiveTarget": live,
                                "event": "first_exact_hit",
                                "globalCommandIndex": task["globalCommandIndex"],
                                "logicalTaskId": task["logicalTaskId"],
                                "staged": row["staged"],
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )

            append_jsonl(args.results, row)
            completed_this_run += 1
            progress = {
                "completedThisRun": completed_this_run,
                "elapsedSeconds": round(time.monotonic() - started, 3),
                "event": "completed",
                "exactCertified": row["exactCertified"],
                "globalCommandIndex": task["globalCommandIndex"],
                "logicalTaskId": task["logicalTaskId"],
                "staged": row["staged"],
                "workerFailure": row["workerFailure"],
            }
            print(json.dumps(progress, sort_keys=True), flush=True)
            if completed_this_run % 10 == 0:
                write_summary(
                    args.summary,
                    {
                        "bank": str(args.bank.resolve()),
                        "bankSha256": bank_hash,
                        "completedThisRun": completed_this_run,
                        "elapsedSeconds": round(time.monotonic() - started, 3),
                        "exactCertified": exact_hits,
                        "failed": failures,
                        "globalIndexParity": "odd",
                        "assigned": len(assigned),
                        "currentValuable": len(selected),
                        "pendingAtStart": len(pending),
                        "resumed": len(completed),
                        "selected": len(selected),
                        "staged": staged_hits,
                        "status": "running",
                        "workers": args.workers,
                    },
                )

    summary = {
        "bank": str(args.bank.resolve()),
        "bankSha256": bank_hash,
        "completedThisRun": completed_this_run,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "exactCertified": exact_hits,
        "failed": failures,
        "globalIndexParity": "odd",
        "assigned": len(assigned),
        "currentValuable": len(selected),
        "hits": str(args.hits.resolve()),
        "manifest": str(args.manifest.resolve()),
        "pendingAtStart": len(pending),
        "results": str(args.results.resolve()),
        "resumed": len(completed),
        "selected": len(selected),
        "staged": staged_hits,
        "status": "complete",
        "workers": args.workers,
    }
    write_summary(args.summary, summary)
    print(json.dumps({"event": "complete", **summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
