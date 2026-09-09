#!/usr/bin/env python3
"""Extract and exactly assign useful factors from multi-orbit pair resolvents."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"
ORBIT_MAP = ROOT / "data" / "pair_orbit_map.jsonl"
SIGNATURE_MAP = ROOT / "data" / "pair_signature_map.jsonl"
WORKER = ROOT / "pair_sum_one.sage.py"
OUTPUT = ROOT / "data" / "pair_sum_multi_candidates.jsonl"
SUMMARY = ROOT / "data" / "pair_sum_multi_summary.json"
MANIFEST = ROOT / "outbox" / "pair_sum_multi_gold.txt"
DEFAULT_MAX_SOURCE_POLYNOMIALS_PER_PAIR = 1


def load_jsonl(path: Path, key: str) -> dict[str, dict]:
    return {
        str(row[key]): row
        for row in (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        )
    }


def load_tasks(
    max_source_polynomials_per_pair: int = DEFAULT_MAX_SOURCE_POLYNOMIALS_PER_PAIR,
    explicit_source_pairs: set[tuple[str, int]] | None = None,
    explicit_source_rows: set[tuple[str, int]] | None = None,
) -> list[dict]:
    if max_source_polynomials_per_pair < 1:
        raise ValueError("max_source_polynomials_per_pair must be positive")
    orbits = load_jsonl(ORBIT_MAP, "sourceLabel")
    profiles = load_jsonl(SIGNATURE_MAP, "sourceLabel")
    with sqlite3.connect(DB_PATH) as conn:
        gold_labels = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT t.label
                FROM targets AS t
                LEFT JOIN baseline_pairs AS b
                  ON b.label=t.label AND b.r=t.r
                WHERE t.team_count=0 AND b.label IS NULL
                """
            )
        }
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
                JOIN polynomials AS p USING(submission_id, polynomial_index)
                WHERE v.status = 'accepted'
            )
            SELECT submission_id, polynomial_index, label, r
            FROM ranked_sources
            WHERE source_rank <= ?
            ORDER BY label, r, submission_id, polynomial_index
            """,
            (max_source_polynomials_per_pair,),
        ).fetchall()
    tasks = []
    for submission_id, polynomial_index, source_label, source_r in verified:
        source_pair = (str(source_label), int(source_r))
        source_row = (str(submission_id), int(polynomial_index))
        if explicit_source_pairs is not None and source_pair not in explicit_source_pairs:
            continue
        if explicit_source_rows is not None and source_row not in explicit_source_rows:
            continue
        orbit = orbits.get(str(source_label))
        profile = profiles.get(str(source_label))
        if orbit is None or profile is None or profile.get("status") != "certified":
            continue
        if int(orbit["length24OrbitCount"]) <= 1:
            continue
        target_labels = {str(row["targetLabel"]) for row in orbit["targets"]}
        if (
            explicit_source_pairs is None
            and explicit_source_rows is None
            and not (target_labels & gold_labels)
        ):
            continue
        tasks.append(
            {
                "submissionId": str(submission_id),
                "polynomialIndex": int(polynomial_index),
                "sourceLabel": str(source_label),
                "sourceR": int(source_r),
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
        "--all-degree-24",
        "--transforms",
        "1,2,3,5,7",
        "--reduce",
        "best",
    ]
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
        return {**task, "status": "timeout"}
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            **task,
            "status": "invalid_output",
            "error": str(exc),
            "stderrTail": completed.stderr[-2000:],
        }
    return {**task, **result, "workerExitCode": completed.returncode}


def stable_assignments(result: dict, signature_row: dict) -> tuple[list[dict], dict]:
    candidates = result.get("candidates") or []
    actual_r = sorted(int(row["targetR"]) for row in candidates)
    compatible = []
    for profile in signature_row.get("profiles") or []:
        if int(profile["sourceR"]) != int(result["sourceR"]):
            continue
        predicted_r = sorted(
            int(row["targetR"]) for row in profile["orbitSignatures"]
        )
        if predicted_r == actual_r:
            compatible.append(profile)
    proof = {
        "actualFactorR": actual_r,
        "compatibleClassIndexes": [int(row["classIndex"]) for row in compatible],
    }
    if not compatible:
        proof["reason"] = "no complex-conjugation profile matches factor signatures"
        return [], proof

    assignments = []
    for r in sorted(set(actual_r)):
        label_multisets = []
        for profile in compatible:
            labels = sorted(
                str(row["targetLabel"])
                for row in profile["orbitSignatures"]
                if int(row["targetR"]) == r
            )
            label_multisets.append(labels)
        if any(labels != label_multisets[0] for labels in label_multisets[1:]):
            continue
        labels = label_multisets[0]
        if len(set(labels)) != 1:
            continue
        label = labels[0]
        factors = [row for row in candidates if int(row["targetR"]) == r]
        if len(factors) != len(labels):
            raise ArithmeticError("factor/profile multiplicity mismatch")
        for factor in factors:
            assignments.append({**factor, "targetLabel": label, "targetR": r})
    proof["assignedFactors"] = len(assignments)
    proof["totalFactors"] = len(candidates)
    return assignments, proof


def annotate(rows: list[dict], signatures: dict[str, dict]) -> list[dict]:
    assigned = []
    with sqlite3.connect(DB_PATH) as conn:
        for result in rows:
            if result.get("status") != "certified_multi":
                continue
            factors, proof = stable_assignments(
                result, signatures[result["sourceLabel"]]
            )
            result["assignmentProof"] = proof
            for factor in factors:
                target = conn.execute(
                    "SELECT t,team_count FROM targets WHERE label=? AND r=?",
                    (factor["targetLabel"], factor["targetR"]),
                ).fetchone()
                baseline = conn.execute(
                    "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
                    (factor["targetLabel"], factor["targetR"]),
                ).fetchone()
                owned = conn.execute(
                    "SELECT 1 FROM verifications WHERE label=? AND r=? AND status='accepted' LIMIT 1",
                    (factor["targetLabel"], factor["targetR"]),
                ).fetchone()
                factor.update(
                    {
                        "sourceSubmissionId": result["sourceSubmissionId"],
                        "sourcePolynomialIndex": result["sourcePolynomialIndex"],
                        "sourceLabel": result["sourceLabel"],
                        "sourceR": result["sourceR"],
                        "targetT": int(target[0]) if target else None,
                        "teamCount": int(target[1]) if target else None,
                        "baseline": baseline is not None,
                        "locallyOwned": owned is not None,
                        "assignmentProof": proof,
                    }
                )
                factor["valuableGold"] = bool(
                    target is not None
                    and int(target[1]) == 0
                    and baseline is None
                    and owned is None
                )
                assigned.append(factor)
    return assigned


def select_gold(assigned: list[dict]) -> list[dict]:
    best: dict[tuple[str, int], dict] = {}
    for row in assigned:
        if not row["valuableGold"]:
            continue
        key = (row["targetLabel"], int(row["targetR"]))
        incumbent = best.get(key)
        if incumbent is None or int(row["polynomialDiscriminantAbs"]) < int(
            incumbent["polynomialDiscriminantAbs"]
        ):
            best[key] = row
    return sorted(best.values(), key=lambda row: (row["targetT"], row["targetR"]))


def write(
    rows: list[dict],
    assigned: list[dict],
    selected: list[dict],
    output: Path = OUTPUT,
    summary_path: Path = SUMMARY,
    manifest: Path = MANIFEST,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(output)
    manifest_temporary = manifest.with_suffix(".txt.tmp")
    manifest_temporary.write_text(
        "".join(f"{row['coefficientLine']}\n" for row in selected),
        encoding="utf-8",
    )
    manifest_temporary.replace(manifest)
    summary = {
        "workersCompleted": len(rows),
        "workersCertified": sum(row.get("status") == "certified_multi" for row in rows),
        "assignedFactors": len(assigned),
        "gold": len(selected),
        "manifest": str(manifest),
        "selectedPairs": [
            {
                "label": row["targetLabel"],
                "r": row["targetR"],
                "sourceLabel": row["sourceLabel"],
                "sourceR": row["sourceR"],
                "coefficientSha256": row["coefficientSha256"],
            }
            for row in selected
        ],
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--source",
        action="append",
        metavar="24Tn/rN",
        help="restrict the run to an explicit source pair; repeat as needed",
    )
    parser.add_argument(
        "--source-row",
        action="append",
        metavar="SUBMISSION_ID:INDEX",
        help="restrict the run to an exact verified source row; repeat as needed",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
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
    if args.source and args.source_row:
        parser.error("--source and --source-row are mutually exclusive")
    explicit_source_pairs = None
    if args.source:
        explicit_source_pairs = set()
        for value in args.source:
            try:
                label, raw_r = value.split("/r", 1)
                r = int(raw_r)
            except (ValueError, TypeError):
                parser.error(f"invalid --source {value!r}; expected 24Tn/rN")
            if not label.startswith("24T") or not label[3:].isdigit():
                parser.error(f"invalid --source label {label!r}")
            explicit_source_pairs.add((label, r))
    explicit_source_rows = None
    if args.source_row:
        explicit_source_rows = set()
        for value in args.source_row:
            try:
                submission_id, raw_index = value.rsplit(":", 1)
                polynomial_index = int(raw_index)
            except (ValueError, TypeError):
                parser.error(
                    f"invalid --source-row {value!r}; expected SUBMISSION_ID:INDEX"
                )
            if not submission_id.startswith("sub_") or polynomial_index < 0:
                parser.error(
                    f"invalid --source-row {value!r}; expected SUBMISSION_ID:INDEX"
                )
            explicit_source_rows.add((submission_id, polynomial_index))
    tasks = load_tasks(
        args.max_source_polynomials_per_pair,
        explicit_source_pairs,
        explicit_source_rows,
    )
    if explicit_source_pairs is not None:
        found_pairs = {(row["sourceLabel"], row["sourceR"]) for row in tasks}
        missing_pairs = sorted(explicit_source_pairs - found_pairs)
        if missing_pairs:
            parser.error(f"no eligible verified source row for: {missing_pairs}")
    if explicit_source_rows is not None:
        found_rows = {
            (row["submissionId"], row["polynomialIndex"]) for row in tasks
        }
        missing_rows = sorted(explicit_source_rows - found_rows)
        if missing_rows:
            parser.error(f"no eligible verified source row for: {missing_rows}")
    signatures = load_jsonl(SIGNATURE_MAP, "sourceLabel")
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(run_task, task, args.timeout): task for task in tasks}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            row = future.result()
            rows.append(row)
            print(
                f"completed {index}/{len(tasks)}: {row['sourceLabel']} "
                f"r={row['sourceR']} status={row.get('status')}",
                file=sys.stderr,
                flush=True,
            )
    assigned = annotate(rows, signatures)
    selected = select_gold(assigned)
    write(rows, assigned, selected, args.output, args.summary, args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
