#!/usr/bin/env python3
"""Recover exact bases for the remaining blocked Cross1500 stage-1 tasks.

The supervisor audits the existing ledger plus the recovered HTML manifests,
removes quotients already tested by the immutable bank or any alignment
artifact, and launches six process-isolated Sage alignment workers.  It stops
launching new candidates after the first newly unblocked target so the exact
character pilot can be run immediately.  There are no network or submission
operations.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import glob
import hashlib
import json
import sqlite3
import subprocess
import time
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_LOCKED_SUMMARY = DATA / "agent_gold_a_cross1500_character_bank_summary.json"
DEFAULT_RESULTS = DATA / "agent_blocked_base_recovery_results.jsonl"
DEFAULT_SUMMARY = DATA / "agent_blocked_base_recovery_summary.json"
DEFAULT_ARTIFACT_DIR = DATA / "agent_blocked_base_recovery_alignments"
WORKER = ROOT / "agent_blocked_base_align_worker.sage.py"

TARGETS = {
    16: ("24T16112", "24T16114"),
    70: ("24T19171", "24T19172"),
    71: ("24T19174",),
    77: ("24T19320",),
    87: ("24T19667",),
}

# These are the exact labels of the full 2^11 character actions obtained from
# the index-two normal subgroups of each abstract degree-12 quotient.  A field
# with any other degree-24 label cannot be a source for this locked direct
# character-kernel construction, even if it has some unrelated 2-block system
# with the same quotient T-number.
DIRECT_SOURCE_LABELS = {
    16: {"24T16112", "24T16113", "24T16114"},
    70: {"24T19171", "24T19172", "24T19173"},
    71: {"24T19174", "24T19175"},
    77: {"24T19318", "24T19319", "24T19320", "24T19321", "24T19322", "24T19323"},
    87: {"24T19665", "24T19666", "24T19667"},
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def even_quotient(coefficients: str) -> tuple[str, str] | None:
    try:
        values = [int(value) for value in coefficients.split(",")]
    except ValueError:
        return None
    if (
        len(values) != 25
        or values[-1] != 1
        or values[0] <= 0
        or any(values[index] for index in range(1, 25, 2))
    ):
        return None
    line = ",".join(str(value) for value in values[::2])
    return line, hashlib.sha256(line.encode()).hexdigest()


def source_labels(action_map: Path) -> tuple[dict[int, set[str]], dict[tuple[int, str], int]]:
    labels = {quotient_t: set() for quotient_t in TARGETS}
    counts = {}
    for line in action_map.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        label = str(row["sourceLabel"])
        system_count = int(row["systemCount"])
        for system in row["systems"]:
            quotient_t = int(system["blockActionT12"])
            if quotient_t in labels:
                labels[quotient_t].add(label)
                key = (quotient_t, label)
                counts[key] = min(system_count, counts.get(key, system_count))
    return labels, counts


def candidates(db: Path, labels_by_q: dict[int, set[str]], system_counts: dict) -> dict[int, list[dict]]:
    label_to_qs: dict[str, set[int]] = defaultdict(set)
    for quotient_t, labels in labels_by_q.items():
        for label in labels:
            label_to_qs[label].add(quotient_t)
    output: dict[int, list[dict]] = defaultdict(list)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        labels = sorted(label_to_qs)
        for offset in range(0, len(labels), 500):
            chunk = labels[offset : offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            query = f"""
                SELECT v.label,v.field_disc_abs,v.submission_id,
                       v.polynomial_index,p.coefficients,p.coefficient_hash
                FROM verifications AS v
                JOIN polynomials AS p USING(submission_id,polynomial_index)
                WHERE v.status='accepted' AND v.scoreable=1
                  AND v.label IN ({placeholders})
            """
            for row in connection.execute(query, chunk):
                quotient = even_quotient(str(row[4]))
                if quotient is None:
                    continue
                quotient_line, quotient_hash = quotient
                common = {
                    "coefficientBytes": len(str(row[4]).encode()),
                    "coefficientSha256": str(row[5]),
                    "fieldDiscAbs": str(row[1]) if row[1] else None,
                    "label": str(row[0]),
                    "polynomialIndex": int(row[3]),
                    "quotientLine": quotient_line,
                    "quotientSha256": quotient_hash,
                    "submissionId": str(row[2]),
                }
                for quotient_t in label_to_qs[common["label"]]:
                    output[quotient_t].append(
                        {
                            **common,
                            "blockSystemCount": system_counts[
                                (quotient_t, common["label"])
                            ],
                            "quotientT": quotient_t,
                        }
                    )
    finally:
        connection.close()
    for quotient_t, rows in output.items():
        rows.sort(
            key=lambda row: (
                row["blockSystemCount"] != 1,
                row["coefficientBytes"],
                len(row["fieldDiscAbs"] or "9" * 100000),
                row["fieldDiscAbs"] or "9" * 100000,
                row["coefficientSha256"],
            )
        )
    return output


def prior_quotients(db: Path, locked_summary: Path) -> tuple[dict[int, set[str]], list[dict]]:
    tested = {quotient_t: set() for quotient_t in TARGETS}
    provenance = []
    summary = json.loads(locked_summary.read_text(encoding="utf-8"))
    for key, rows in summary.get("failureAudit", {}).items():
        quotient_t = int(key)
        if quotient_t not in tested:
            continue
        for row in rows:
            quotient_hash = str(row["quotientSha256"])
            tested[quotient_t].add(quotient_hash)
            provenance.append(
                {
                    "kind": "locked_failure_audit",
                    "quotientSha256": quotient_hash,
                    "quotientT": quotient_t,
                }
            )

    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        for name in glob.glob(str(DATA / "*character_alignment_q*_base*.json")):
            path = Path(name)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                quotient_t = int(payload["alignment"]["quotientT"])
                source = payload["source"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if quotient_t not in tested:
                continue
            row = connection.execute(
                "SELECT coefficients FROM polynomials WHERE submission_id=? AND polynomial_index=?",
                (str(source["submissionId"]), int(source["polynomialIndex"])),
            ).fetchone()
            quotient = even_quotient(str(row[0])) if row else None
            if quotient is None:
                continue
            quotient_hash = quotient[1]
            tested[quotient_t].add(quotient_hash)
            provenance.append(
                {
                    "artifact": str(path.relative_to(ROOT)),
                    "artifactSha256": sha256_path(path),
                    "kind": "existing_alignment_artifact",
                    "quotientSha256": quotient_hash,
                    "quotientT": quotient_t,
                }
            )
    finally:
        connection.close()
    return tested, provenance


def recovered_manifest_audit(db: Path) -> list[dict]:
    paths = sorted((DATA / "agent_gold_b_html_wave_manifests").glob("*.txt"))
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    result = []
    try:
        for path in paths:
            lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            hashes = {hashlib.sha256(line.encode()).hexdigest() for line in lines}
            matched = 0
            relevant = 0
            for offset in range(0, len(hashes), 500):
                chunk = list(hashes)[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                query = f"""
                    SELECT COUNT(*),SUM(CASE WHEN v.scoreable=1 THEN 1 ELSE 0 END)
                    FROM polynomials AS p
                    LEFT JOIN verifications AS v USING(submission_id,polynomial_index)
                    WHERE p.coefficient_hash IN ({placeholders})
                """
                row = connection.execute(query, chunk).fetchone()
                matched += int(row[0] or 0)
                relevant += int(row[1] or 0)
            result.append(
                {
                    "lineOccurrences": len(lines),
                    "matchedLedgerRows": matched,
                    "path": str(path.relative_to(ROOT)),
                    "scoreableLedgerRows": relevant,
                    "sha256": sha256_path(path),
                    "uniqueCoefficientHashes": len(hashes),
                }
            )
    finally:
        connection.close()
    return result


def execute(task: dict, db: Path, artifact_dir: Path, timeout: int) -> dict:
    output = artifact_dir / (
        f"q{task['quotientT']}__{task['submissionId']}__"
        f"p{task['polynomialIndex']}__{task['quotientSha256'][:12]}.json"
    )
    command = [
        "sage",
        "-python",
        str(WORKER),
        "--db",
        str(db),
        "--submission-id",
        task["submissionId"],
        "--polynomial-index",
        str(task["polynomialIndex"]),
        "--quotient-t",
        str(task["quotientT"]),
        "--output",
        str(output),
    ]
    for label in TARGETS[task["quotientT"]]:
        command.extend(["--target-label", label])
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
    except subprocess.TimeoutExpired as exc:
        return {
            **{key: value for key, value in task.items() if key != "quotientLine"},
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "status": "timeout",
            "stderr": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
        }
    stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    parsed = None
    if stdout_lines:
        try:
            parsed = json.loads(stdout_lines[-1])
        except json.JSONDecodeError:
            parsed = None
    if parsed is None:
        status = "worker_error" if completed.returncode else "invalid_worker_output"
        parsed = {"status": status}
    return {
        **{key: value for key, value in task.items() if key != "quotientLine"},
        **parsed,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "returnCode": completed.returncode,
        "stderr": completed.stderr[-1000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--action-map", type=Path, default=DEFAULT_ACTION_MAP)
    parser.add_argument("--locked-summary", type=Path, default=DEFAULT_LOCKED_SUMMARY)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--stop-on-first-hit", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="reuse quotient hashes already recorded in --results",
    )
    args = parser.parse_args()
    if args.workers != 6:
        raise ValueError("blocked-base recovery must use exactly six workers")

    labels_by_q, system_counts = source_labels(args.action_map)
    all_candidates = candidates(args.db, labels_by_q, system_counts)
    tested, tested_provenance = prior_quotients(args.db, args.locked_summary)
    resumed_rows = []
    if args.resume and args.results.exists():
        for line in args.results.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            quotient_t = int(row["quotientT"])
            tested[quotient_t].add(str(row["quotientSha256"]))
            resumed_rows.append(row)
    unseen = []
    counts = {}
    for quotient_t in sorted(TARGETS):
        unique = {}
        for row in all_candidates.get(quotient_t, []):
            unique.setdefault(row["quotientSha256"], row)
        direct = {
            key: row
            for key, row in unique.items()
            if row["label"] in DIRECT_SOURCE_LABELS[quotient_t]
        }
        pending = [row for key, row in direct.items() if key not in tested[quotient_t]]
        counts[str(quotient_t)] = {
            "candidateRows": len(all_candidates.get(quotient_t, [])),
            "directActionUniqueQuotients": len(direct),
            "excludedNonDirectActionUniqueQuotients": len(unique) - len(direct),
            "priorTestedUniqueQuotients": len(tested[quotient_t]),
            "uniqueQuotients": len(unique),
            "unseenUniqueQuotients": len(pending),
        }
        unseen.extend(pending)

    # Maximize immediately unblocked logical tasks, then prefer smaller scans.
    task_weight = {16: 11, 70: 12, 71: 6, 77: 5, 87: 1}
    unseen.sort(key=lambda row: (-task_weight[row["quotientT"]], row["quotientT"]))
    if not args.resume:
        args.results.unlink(missing_ok=True)
    started = time.monotonic()
    launched = 0
    completed_count = 0
    hits = [row for row in resumed_rows if row.get("status") == "aligned_target"]
    next_index = 0
    active = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        while next_index < len(unseen) and len(active) < args.workers:
            task = unseen[next_index]
            next_index += 1
            launched += 1
            active[pool.submit(execute, task, args.db, args.artifact_dir, args.timeout)] = task
        while active:
            done, _ = concurrent.futures.wait(
                active, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                active.pop(future)
                row = future.result()
                append_jsonl(args.results, row)
                completed_count += 1
                if row.get("status") == "aligned_target":
                    hits.append(row)
                print(
                    json.dumps(
                        {
                            "completed": completed_count,
                            "event": "base_alignment",
                            "hits": len(hits),
                            "launched": launched,
                            "quotientT": row["quotientT"],
                            "status": row.get("status"),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            while (
                next_index < len(unseen)
                and len(active) < args.workers
                and not (args.stop_on_first_hit and hits)
            ):
                task = unseen[next_index]
                next_index += 1
                launched += 1
                active[pool.submit(execute, task, args.db, args.artifact_dir, args.timeout)] = task

    manifest_audit = recovered_manifest_audit(args.db)
    results_hash = sha256_path(args.results) if args.results.exists() else hashlib.sha256(b"").hexdigest()
    summary = {
        "actionMap": str(args.action_map.resolve()),
        "actionMapSha256": sha256_path(args.action_map),
        "alignmentHits": hits,
        "candidateCounts": counts,
        "completed": completed_count,
        "database": str(args.db.resolve()),
        "databaseLogicalCounts": {},
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "launched": launched,
        "lockedSummary": str(args.locked_summary.resolve()),
        "lockedSummarySha256": sha256_path(args.locked_summary),
        "networkCalls": 0,
        "priorTestedProvenance": tested_provenance,
        "recoveredManifestAudit": manifest_audit,
        "results": str(args.results.resolve()),
        "resultsSha256": results_hash,
        "resumedResultRows": len(resumed_rows),
        "status": "stopped_after_first_alignment" if hits and args.stop_on_first_hit else "exhaustive_complete",
        "submissionCalls": 0,
        "unlaunchedAfterFirstHit": len(unseen) - launched,
        "workers": args.workers,
    }
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        summary["databaseLogicalCounts"] = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("submissions", "polynomials", "verifications")
        }
    finally:
        connection.close()
    write_atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"event": "complete", **{key: summary[key] for key in ("completed", "elapsedSeconds", "launched", "resultsSha256", "status", "unlaunchedAfterFirstHit")}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
