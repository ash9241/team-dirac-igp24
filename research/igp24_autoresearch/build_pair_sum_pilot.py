#!/usr/bin/env python3
"""Build a conservative exact unordered-pair-action pilot manifest."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"
ORBIT_MAP_PATH = ROOT / "data" / "pair_orbit_map.jsonl"
WORKER = ROOT / "pair_sum_one.sage.py"
OUTPUT = ROOT / "data" / "pair_sum_candidates.jsonl"
SUMMARY = ROOT / "data" / "pair_sum_pilot_summary.json"
MANIFEST = ROOT / "outbox" / "pair_sum_pilot.txt"
DEFAULT_MAX_SOURCE_POLYNOMIALS_PER_PAIR = 1


def load_orbit_rows() -> dict[str, dict]:
    rows = {}
    for line in ORBIT_MAP_PATH.read_text(encoding="utf-8").splitlines():
        if line:
            row = json.loads(line)
            rows[row["sourceLabel"]] = row
    return rows


def load_tasks(
    max_source_polynomials_per_pair: int = DEFAULT_MAX_SOURCE_POLYNOMIALS_PER_PAIR,
) -> list[dict]:
    if max_source_polynomials_per_pair < 1:
        raise ValueError("max_source_polynomials_per_pair must be positive")
    orbit_rows = load_orbit_rows()
    eligible = {
        label: row
        for label, row in orbit_rows.items()
        if row["length24OrbitCount"] == 1
        and row["targets"][0]["targetLabel"] != label
    }
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        verified = conn.execute(
            """
            WITH ranked_sources AS (
                SELECT
                    v.submission_id,
                    v.polynomial_index,
                    v.label,
                    v.r,
                    ROW_NUMBER() OVER (
                        PARTITION BY v.label, v.r
                        ORDER BY v.submission_id, v.polynomial_index
                    ) AS source_rank
                FROM verifications AS v
                JOIN polynomials AS p USING (submission_id, polynomial_index)
                WHERE v.scoreable = 1
            )
            SELECT submission_id, polynomial_index, label, r
            FROM ranked_sources
            WHERE source_rank <= ?
            ORDER BY label, r, submission_id, polynomial_index
            """,
            (max_source_polynomials_per_pair,),
        ).fetchall()
        useful_target_labels = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT t.label
                FROM targets AS t
                LEFT JOIN baseline_pairs AS b
                  ON b.label=t.label AND b.r=t.r
                WHERE t.team_count <= 1 AND b.label IS NULL
                """
            )
        }
    tasks = []
    for row in verified:
        source_label = str(row["label"])
        orbit = eligible.get(source_label)
        if orbit is None:
            continue
        target_label = str(orbit["targets"][0]["targetLabel"])
        if target_label not in useful_target_labels:
            continue
        tasks.append(
            {
                "submissionId": str(row["submission_id"]),
                "polynomialIndex": int(row["polynomial_index"]),
                "sourceLabel": source_label,
                "sourceR": int(row["r"]),
                "targetLabel": target_label,
            }
        )
    return tasks


def run_task(task: dict, timeout: int) -> dict:
    command = [
        "sage",
        "-python",
        str(WORKER),
        task["submissionId"],
        str(task["polynomialIndex"]),
        "--expected-target",
        task["targetLabel"],
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
        return {**task, "status": "worker_timeout", "elapsedSeconds": timeout}
    result = {**task, "workerExitCode": completed.returncode}
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            **result,
            "status": "invalid_worker_output",
            "error": str(exc),
            "stderrTail": completed.stderr[-2000:],
            "elapsedSeconds": round(time.monotonic() - started, 3),
        }
    result.update(payload)
    if completed.returncode != 0 and result.get("status") == "certified":
        result["status"] = "worker_error"
    result["workerStderrTail"] = completed.stderr[-2000:]
    result["workerWallSeconds"] = round(time.monotonic() - started, 3)
    return result


def annotate_target_state(rows: list[dict]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        for row in rows:
            if row.get("status") != "certified":
                continue
            label = row["targetLabel"]
            r = int(row["targetR"])
            target = conn.execute(
                "SELECT team_count,minimum_disc_abs FROM targets WHERE label=? AND r=?",
                (label, r),
            ).fetchone()
            baseline = conn.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
                (label, r),
            ).fetchone()
            owned = conn.execute(
                "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
                (label, r),
            ).fetchone()[0]
            row["targetState"] = {
                "known": target is not None,
                "teamCount": int(target[0]) if target else None,
                "minimumDiscAbs": str(target[1]) if target and target[1] is not None else None,
                "baseline": baseline is not None,
                "ownedInLocalLedger": bool(owned),
            }
            row["valuable"] = bool(
                target is not None
                and int(target[0]) <= 1
                and baseline is None
                and not owned
            )
            row["valueTier"] = (
                "gold"
                if row["valuable"] and int(target[0]) == 0
                else "solo"
                if row["valuable"] and int(target[0]) == 1
                else "skip"
            )


def choose_manifest_rows(rows: list[dict]) -> list[dict]:
    best: dict[tuple[str, int], dict] = {}
    for row in rows:
        if not row.get("valuable"):
            continue
        key = (str(row["targetLabel"]), int(row["targetR"]))
        incumbent = best.get(key)
        if incumbent is None or int(row["polynomialDiscriminantAbs"]) < int(
            incumbent["polynomialDiscriminantAbs"]
        ):
            best[key] = row
    return sorted(best.values(), key=lambda row: (row["valueTier"] != "gold", row["targetT"], row["targetR"]))


def write_outputs(rows: list[dict], selected: list[dict], tasks: list[dict]) -> None:
    temporary = OUTPUT.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda item: (item["sourceLabel"], item["sourceR"])):
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(OUTPUT)

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    manifest_temporary = MANIFEST.with_suffix(".txt.tmp")
    manifest_temporary.write_text(
        "".join(f"{row['coefficientLine']}\n" for row in selected),
        encoding="utf-8",
    )
    manifest_temporary.replace(MANIFEST)

    summary = {
        "tasks": len(tasks),
        "certified": sum(row.get("status") == "certified" for row in rows),
        "failed": sum(row.get("status") != "certified" for row in rows),
        "gold": sum(row.get("valueTier") == "gold" for row in selected),
        "solo": sum(row.get("valueTier") == "solo" for row in selected),
        "selected": len(selected),
        "goldUpperBoundPoints": sum(row.get("valueTier") == "gold" for row in selected),
        "soloUpperBoundPoints": 0.5 * sum(row.get("valueTier") == "solo" for row in selected),
        "manifest": str(MANIFEST),
        "results": str(OUTPUT),
        "selectedPairs": [
            {
                "label": row["targetLabel"],
                "r": row["targetR"],
                "tier": row["valueTier"],
                "sourceLabel": row["sourceLabel"],
                "sourceR": row["sourceR"],
                "coefficientSha256": row["coefficientSha256"],
            }
            for row in selected
        ],
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--max-source-polynomials-per-pair",
        type=int,
        default=DEFAULT_MAX_SOURCE_POLYNOMIALS_PER_PAIR,
        help=(
            "maximum verified source rows to dispatch for each source "
            "(label,r); retained rows keep their original proof provenance"
        ),
    )
    args = parser.parse_args()
    if args.max_source_polynomials_per_pair < 1:
        parser.error("--max-source-polynomials-per-pair must be positive")
    tasks = load_tasks(args.max_source_polynomials_per_pair)
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(run_task, task, args.timeout): task for task in tasks
        }
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            rows.append(result)
            print(
                f"completed {index}/{len(tasks)}: {result['sourceLabel']} "
                f"r={result['sourceR']} status={result.get('status')}",
                file=sys.stderr,
                flush=True,
            )
    annotate_target_state(rows)
    selected = choose_manifest_rows(rows)
    write_outputs(rows, selected, tasks)
    return 0 if all(row.get("status") == "certified" for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
