#!/usr/bin/env python3
"""Incrementally harvest exact single-orbit pair actions beyond the gold filter.

``build_pair_sum_pilot.py`` deliberately restricted work to target labels that
were gold or solo at its snapshot.  This isolated follow-on keeps the same
exact Sage worker and certificates, but considers every mapped nonidentity
single-orbit action whose target label still has at least one nonbaseline pair
that is absent from the local ledger.  Production pilot outputs are read as a
tested-source set and are never overwritten.
"""

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
PRODUCTION_RESULTS = ROOT / "data" / "pair_sum_candidates.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "pair_sum_single_expanded_candidates.jsonl"
DEFAULT_SUMMARY = ROOT / "data" / "pair_sum_single_expanded_summary.json"


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


def source_pair(row: dict) -> tuple[str, int]:
    return str(row["sourceLabel"]), int(row["sourceR"])


def source_key(row: dict) -> tuple[str, int]:
    return (
        str(row.get("sourceSubmissionId", row.get("submissionId"))),
        int(row.get("sourcePolynomialIndex", row.get("polynomialIndex"))),
    )


def load_orbits(*, include_same_label: bool = False) -> dict[str, dict]:
    result = {}
    for row in read_jsonl(ORBIT_MAP_PATH):
        label = str(row["sourceLabel"])
        if label in result:
            raise ValueError(f"duplicate orbit-map source label: {label}")
        if int(row["length24OrbitCount"]) == 1:
            target_label = str(row["targets"][0]["targetLabel"])
            if (
                (include_same_label and target_label == label)
                or (not include_same_label and target_label != label)
            ):
                result[label] = row
    return result


def load_tasks(
    existing_rows: list[dict],
    *,
    include_same_label: bool = False,
    max_target_min_team_count: int | None,
    limit: int | None,
) -> tuple[list[dict], dict]:
    orbits = load_orbits(include_same_label=include_same_label)
    tested_pairs = {
        source_pair(row)
        for row in [*read_jsonl(PRODUCTION_RESULTS), *existing_rows]
        if row.get("sourceLabel") is not None and row.get("sourceR") is not None
    }
    tested_keys = {
        source_key(row)
        for row in [*read_jsonl(PRODUCTION_RESULTS), *existing_rows]
        if row.get("sourceSubmissionId", row.get("submissionId")) is not None
        and row.get("sourcePolynomialIndex", row.get("polynomialIndex")) is not None
    }

    connection = sqlite3.connect(f"file:{DB_PATH.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        owned_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
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
                    v.r,
                    length(p.original_line) AS coefficient_bytes,
                    ROW_NUMBER() OVER (
                        PARTITION BY v.label,v.r
                        ORDER BY v.submission_id,v.polynomial_index
                    ) AS source_rank
                FROM verifications AS v
                JOIN polynomials AS p USING(submission_id,polynomial_index)
                WHERE v.scoreable=1
            )
            SELECT * FROM ranked_sources
            WHERE source_rank=1
            ORDER BY label,r,submission_id,polynomial_index
            """
        ).fetchall()
    finally:
        connection.close()

    tasks = []
    skip_counts: dict[str, int] = {}

    def skip(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    for row in rows:
        label = str(row["label"])
        r = int(row["r"])
        orbit = orbits.get(label)
        if orbit is None:
            skip("no_nonidentity_single_orbit")
            continue
        if (label, r) in tested_pairs:
            skip("source_pair_already_tested")
            continue
        key = (str(row["submission_id"]), int(row["polynomial_index"]))
        if key in tested_keys:
            skip("source_hash_provenance_already_tested")
            continue
        target_label = str(orbit["targets"][0]["targetLabel"])
        available = target_values.get(target_label, [])
        if not available:
            skip("target_label_has_no_locally_unowned_nonbaseline_pair")
            continue
        minimum_team_count = min(team_count for _r, team_count in available)
        if (
            max_target_min_team_count is not None
            and minimum_team_count > max_target_min_team_count
        ):
            skip("target_label_above_team_count_cap")
            continue
        tasks.append(
            {
                "submissionId": key[0],
                "polynomialIndex": key[1],
                "sourceLabel": label,
                "sourceR": r,
                "sourceCoefficientBytes": int(row["coefficient_bytes"]),
                "targetLabel": target_label,
                "targetMinTeamCount": minimum_team_count,
                "targetBestPossibleMarginalScore": 1.0 / (minimum_team_count + 1),
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
    eligible_before_limit = len(tasks)
    if limit is not None:
        tasks = tasks[:limit]
    audit = {
        "mappedEligibleSingleSourceLabels": len(orbits),
        "includeSameLabel": include_same_label,
        "testedSourcePairs": len(tested_pairs),
        "testedSourceKeys": len(tested_keys),
        "eligibleBeforeLimit": eligible_before_limit,
        "selectedTasks": len(tasks),
        "skipCounts": dict(sorted(skip_counts.items())),
    }
    return tasks, audit


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
            "workerWallSeconds": round(time.monotonic() - started, 3),
        }
    result.update(payload)
    if completed.returncode != 0 and result.get("status") == "certified":
        result["status"] = "worker_error"
    result["workerStderrTail"] = completed.stderr[-2000:]
    result["workerWallSeconds"] = round(time.monotonic() - started, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--include-same-label",
        action="store_true",
        help=(
            "harvest only length-24 actions whose transitive target label "
            "equals the source label; use a separate output checkpoint"
        ),
    )
    parser.add_argument("--max-target-min-team-count", type=int)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    if args.workers < 1 or args.timeout < 1:
        parser.error("--workers and --timeout must be positive")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if (
        args.max_target_min_team_count is not None
        and args.max_target_min_team_count < 0
    ):
        parser.error("--max-target-min-team-count must be nonnegative")
    protected = {PRODUCTION_RESULTS.resolve(), DB_PATH.resolve(), ORBIT_MAP_PATH.resolve()}
    if args.output.resolve() in protected or args.summary.resolve() in protected:
        parser.error("refusing to overwrite a production input")
    if args.output.resolve() == args.summary.resolve():
        parser.error("--output and --summary must be distinct")

    existing = read_jsonl(args.output)
    tasks, audit = load_tasks(
        existing,
        include_same_label=args.include_same_label,
        max_target_min_team_count=args.max_target_min_team_count,
        limit=args.limit,
    )
    if not tasks:
        print(json.dumps({**audit, "newResults": 0}, indent=2, sort_keys=True))
        return 0

    new_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_task, task, args.timeout): task for task in tasks
        }
        for index, future in enumerate(
            concurrent.futures.as_completed(futures), start=1
        ):
            row = future.result()
            new_results.append(row)
            if index % 25 == 0 or index == len(tasks):
                print(
                    f"completed {index}/{len(tasks)}: {row['sourceLabel']} "
                    f"r={row['sourceR']} status={row.get('status')}",
                    file=sys.stderr,
                    flush=True,
                )

    combined = [*existing, *new_results]
    combined.sort(
        key=lambda row: (
            int(row["sourceLabel"][3:]),
            int(row["sourceR"]),
            str(row.get("sourceSubmissionId", row.get("submissionId"))),
            int(row.get("sourcePolynomialIndex", row.get("polynomialIndex"))),
        )
    )
    write_jsonl_atomic(args.output, combined)
    summary = {
        **audit,
        "workers": args.workers,
        "timeout": args.timeout,
        "maxTargetMinTeamCount": args.max_target_min_team_count,
        "existingResults": len(existing),
        "newResults": len(new_results),
        "newCertified": sum(row.get("status") == "certified" for row in new_results),
        "newFailed": sum(row.get("status") != "certified" for row in new_results),
        "retainedResults": len(combined),
        "medianNewWorkerWallSeconds": (
            sorted(float(row["workerWallSeconds"]) for row in new_results)[
                len(new_results) // 2
            ]
            if new_results
            else None
        ),
        "output": str(args.output.resolve()),
        "summary": str(args.summary.resolve()),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.summary.with_suffix(args.summary.suffix + ".tmp")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["newFailed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
