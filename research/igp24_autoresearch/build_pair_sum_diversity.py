#!/usr/bin/env python3
"""Run an explicit, isolated diversity pass over single pair actions.

This driver is deliberately narrower than ``build_pair_sum_pilot.py``:

* every source ``(24T label, r)`` must be named on the command line;
* only scoreable sources with an authoritative ``exact_nfdisc`` are used;
* at most one untested source polynomial is selected for each exact nfdisc;
* source coefficient hashes retained in earlier pair-sum outputs are skipped;
* only exact one-orbit actions are dispatched; and
* candidates, summary, and manifest use diversity-specific output paths.

The pair-action label is certified by ``pair_orbit_map.jsonl``.  The worker
computes the realized target real-root signature.  A candidate enters the
manifest only when that exact pair is still nonbaseline, team-count zero, and
absent from the local verified ledger at staging time.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "ledger.sqlite3"
ORBIT_MAP_PATH = DATA / "pair_orbit_map.jsonl"
WORKER = ROOT / "pair_sum_one.sage.py"

DEFAULT_CANDIDATE_OUTPUT = DATA / "pair_sum_diversity_candidates.jsonl"
DEFAULT_SUMMARY_OUTPUT = DATA / "pair_sum_diversity_summary.json"
DEFAULT_MANIFEST_OUTPUT = ROOT / "outbox" / "pair_sum_diversity_gold.txt"
DEFAULT_TESTED_INPUTS = (
    DATA / "pair_sum_candidates.jsonl",
    DATA / "pair_sum_multi_candidates.jsonl",
)

# These are authoritative inputs or outputs owned by another production lane.
# The diversity driver must never replace any of them, even when an explicit
# command-line path is misspelled.
PROTECTED_PATHS = frozenset(
    path.resolve()
    for path in (
        DB_PATH,
        ORBIT_MAP_PATH,
        DATA / "pair_signature_map.jsonl",
        DATA / "rank12_route_signature_profiles.jsonl",
        DATA / "pair_sum_candidates.jsonl",
        DATA / "pair_sum_pilot_summary.json",
        DATA / "pair_sum_multi_candidates.jsonl",
        DATA / "pair_sum_multi_summary.json",
        DATA / "frobenius_assignments.jsonl",
        DATA / "frobenius_summary.json",
        ROOT / "outbox" / "pair_sum_pilot.txt",
        ROOT / "outbox" / "pair_sum_multi_gold.txt",
        ROOT / "outbox" / "frobenius_gold.txt",
        ROOT / "outbox" / "four_gold_wave.txt",
        ROOT / "outbox" / "pair_sum_live_gold.txt",
    )
)

SOURCE_PATTERN = re.compile(r"^(24T[1-9][0-9]*)/(?:r)?([0-9]+)$")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"non-object JSON row in {path}:{line_number}")
        rows.append(row)
    return rows


def parse_source_pair(value: str) -> tuple[str, int]:
    match = SOURCE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise argparse.ArgumentTypeError(
            f"invalid source {value!r}; expected 24T<number>/r<even number>"
        )
    label = match.group(1)
    r = int(match.group(2))
    if not 0 <= r <= 24 or r % 2:
        raise argparse.ArgumentTypeError(
            f"invalid real-root count {r}; expected an even integer from 0 to 24"
        )
    return label, r


def read_only_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_orbits(path: Path = ORBIT_MAP_PATH) -> dict[str, dict]:
    rows = read_jsonl(path)
    result: dict[str, dict] = {}
    for row in rows:
        label = str(row["sourceLabel"])
        if label in result:
            raise ValueError(f"duplicate source label in orbit map: {label}")
        result[label] = row
    return result


def validate_output_paths(
    candidate_output: Path, summary_output: Path, manifest_output: Path
) -> None:
    outputs = {
        "candidate": candidate_output.resolve(),
        "summary": summary_output.resolve(),
        "manifest": manifest_output.resolve(),
    }
    if len(set(outputs.values())) != len(outputs):
        raise ValueError("candidate, summary, and manifest paths must be distinct")
    for kind, path in outputs.items():
        if path in PROTECTED_PATHS:
            raise ValueError(
                f"refusing to replace protected production path with {kind} output: {path}"
            )


def source_hash_for_result(
    connection: sqlite3.Connection, row: dict
) -> str | None:
    direct = row.get("sourceCoefficientSha256")
    if direct:
        return str(direct)
    submission_id = row.get("sourceSubmissionId", row.get("submissionId"))
    polynomial_index = row.get(
        "sourcePolynomialIndex", row.get("polynomialIndex")
    )
    if submission_id is None or polynomial_index is None:
        return None
    found = connection.execute(
        """
        SELECT coefficient_hash
        FROM polynomials
        WHERE submission_id=? AND polynomial_index=?
        """,
        (str(submission_id), int(polynomial_index)),
    ).fetchone()
    return str(found[0]) if found is not None else None


def load_tested_source_hashes(
    connection: sqlite3.Connection, paths: Iterable[Path]
) -> tuple[set[str], dict[str, int]]:
    tested: set[str] = set()
    rows_by_path: dict[str, int] = {}
    seen_paths: set[Path] = set()
    for raw_path in paths:
        path = raw_path.resolve()
        if path in seen_paths:
            continue
        seen_paths.add(path)
        rows = read_jsonl(path)
        rows_by_path[str(path)] = len(rows)
        for row in rows:
            digest = source_hash_for_result(connection, row)
            if digest:
                tested.add(digest)
    return tested, rows_by_path


def live_gold_signatures(
    connection: sqlite3.Connection, target_label: str
) -> list[int]:
    return [
        int(row[0])
        for row in connection.execute(
            """
            SELECT t.r
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications
                WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label=?
              AND t.team_count=0
              AND b.label IS NULL
              AND owned.label IS NULL
            ORDER BY t.r
            """,
            (target_label,),
        )
    ]


def validate_routes(
    connection: sqlite3.Connection,
    source_pairs: Iterable[tuple[str, int]],
    orbits: dict[str, dict],
    *,
    require_live_gold_label: bool = True,
) -> dict[tuple[str, int], dict]:
    routes: dict[tuple[str, int], dict] = {}
    for source_label, source_r in sorted(
        set(source_pairs), key=lambda item: (int(item[0][3:]), item[1])
    ):
        orbit = orbits.get(source_label)
        if orbit is None:
            raise ValueError(f"no exact pair-action map for {source_label}")
        targets = list(orbit.get("targets") or [])
        if int(orbit.get("length24OrbitCount", -1)) != 1 or len(targets) != 1:
            raise ValueError(
                f"{source_label} is not a single action: "
                f"length24OrbitCount={orbit.get('length24OrbitCount')}"
            )
        target = targets[0]
        if int(target.get("orbitSize", 24)) != 24:
            raise ValueError(f"{source_label} single target does not have orbit size 24")
        target_label = str(target["targetLabel"])
        gold_r = live_gold_signatures(connection, target_label)
        if require_live_gold_label and not gold_r:
            raise ValueError(
                f"{source_label}/r{source_r} forces {target_label}, "
                "which has no live unowned nonbaseline gold signature"
            )
        routes[(source_label, source_r)] = {
            "sourceLabel": source_label,
            "sourceR": source_r,
            "targetLabel": target_label,
            "targetT": int(target["targetT"]),
            "orbitIndex": int(target["orbitIndex"]),
            "liveGoldTargetR": gold_r,
        }
    return routes


def select_source_tasks(
    connection: sqlite3.Connection,
    routes: dict[tuple[str, int], dict],
    tested_hashes: set[str],
) -> tuple[list[dict], list[dict]]:
    """Choose the shortest untested presentation for each exact nfdisc."""
    tasks = []
    diagnostics = []
    for source_pair, route in routes.items():
        source_label, source_r = source_pair
        rows = connection.execute(
            """
            SELECT
                v.submission_id,
                v.polynomial_index,
                v.field_disc_abs,
                p.coefficient_hash,
                length(p.original_line) AS coefficient_bytes
            FROM verifications AS v
            JOIN polynomials AS p
              USING(submission_id,polynomial_index)
            WHERE v.scoreable=1
              AND v.label=?
              AND v.r=?
              AND v.disc_source='exact_nfdisc'
              AND v.field_disc_abs IS NOT NULL
            ORDER BY length(p.original_line),v.submission_id,v.polynomial_index
            """,
            (source_label, source_r),
        ).fetchall()
        if not rows:
            raise ValueError(
                f"no scoreable exact-nfdisc source for {source_label}/r{source_r}"
            )

        all_nfdiscs = {str(row["field_disc_abs"]) for row in rows}
        tested_rows = [
            row for row in rows if str(row["coefficient_hash"]) in tested_hashes
        ]
        selected_nfdiscs: set[str] = set()
        selected = []
        for row in rows:
            digest = str(row["coefficient_hash"])
            nfdisc = str(row["field_disc_abs"])
            if digest in tested_hashes or nfdisc in selected_nfdiscs:
                continue
            selected_nfdiscs.add(nfdisc)
            selected.append(row)
            tasks.append(
                {
                    **route,
                    "sourceSubmissionId": str(row["submission_id"]),
                    "sourcePolynomialIndex": int(row["polynomial_index"]),
                    "sourceFieldDiscAbs": nfdisc,
                    "sourceCoefficientSha256": digest,
                    "sourceCoefficientBytes": int(row["coefficient_bytes"]),
                }
            )

        nfdiscs_with_untested_hash = {
            str(row["field_disc_abs"])
            for row in rows
            if str(row["coefficient_hash"]) not in tested_hashes
        }
        nfdiscs_with_tested_hash = {
            str(row["field_disc_abs"]) for row in tested_rows
        }
        diagnostics.append(
            {
                "sourceLabel": source_label,
                "sourceR": source_r,
                "targetLabel": route["targetLabel"],
                "liveGoldTargetR": route["liveGoldTargetR"],
                "exactSourceRows": len(rows),
                "distinctExactNfdisc": len(all_nfdiscs),
                "testedSourceRowsSkipped": len(tested_rows),
                "nfdiscsWithTestedHashes": len(nfdiscs_with_tested_hash),
                "nfdiscsNeverTested": len(all_nfdiscs - nfdiscs_with_tested_hash),
                "nfdiscsExhaustedByTestedHashes": len(
                    all_nfdiscs - nfdiscs_with_untested_hash
                ),
                "selectedDistinctNfdisc": len(selected),
            }
        )
    tasks.sort(
        key=lambda row: (
            int(row["sourceLabel"][3:]),
            int(row["sourceR"]),
            int(row["sourceFieldDiscAbs"]),
            row["sourceSubmissionId"],
            int(row["sourcePolynomialIndex"]),
        )
    )
    return tasks, diagnostics


def run_task(
    task: dict,
    *,
    timeout: int,
    transforms: str,
    reduction: str,
) -> dict:
    command = [
        "sage",
        "-python",
        str(WORKER),
        task["sourceSubmissionId"],
        str(task["sourcePolynomialIndex"]),
        "--expected-target",
        task["targetLabel"],
        "--transforms",
        transforms,
        "--reduce",
        reduction,
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
        return {
            **task,
            "status": "worker_timeout",
            "workerWallSeconds": round(time.monotonic() - started, 3),
        }

    common = {
        **task,
        "workerExitCode": int(completed.returncode),
        "workerStderrTail": completed.stderr[-2000:],
        "workerWallSeconds": round(time.monotonic() - started, 3),
    }
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            **common,
            "status": "invalid_worker_output",
            "error": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            **common,
            "status": "invalid_worker_output",
            "error": "last stdout line is not a JSON object",
        }
    result = {**common, **payload}
    if completed.returncode != 0 and result.get("status") == "certified":
        result["status"] = "worker_error"
    if result.get("status") == "certified":
        expected = (
            task["sourceSubmissionId"],
            int(task["sourcePolynomialIndex"]),
            task["sourceLabel"],
            int(task["sourceR"]),
            task["targetLabel"],
        )
        actual = (
            str(result.get("sourceSubmissionId")),
            int(result.get("sourcePolynomialIndex", -1)),
            str(result.get("sourceLabel")),
            int(result.get("sourceR", -1)),
            str(result.get("targetLabel")),
        )
        if actual != expected:
            result["status"] = "worker_provenance_mismatch"
            result["error"] = f"expected provenance {expected!r}, got {actual!r}"
    return result


def annotate_current_target_state(
    connection: sqlite3.Connection, rows: list[dict]
) -> None:
    for row in rows:
        row["valuableGold"] = False
        if row.get("status") != "certified":
            continue
        label = str(row["targetLabel"])
        r = int(row["targetR"])
        target = connection.execute(
            "SELECT team_count FROM targets WHERE label=? AND r=?", (label, r)
        ).fetchone()
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", (label, r)
        ).fetchone()
        owned = connection.execute(
            """
            SELECT 1 FROM verifications
            WHERE label=? AND r=? AND scoreable=1 LIMIT 1
            """,
            (label, r),
        ).fetchone()
        candidate_hash = str(row["coefficientSha256"])
        known_hash = connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (candidate_hash,),
        ).fetchone()
        row["targetState"] = {
            "known": target is not None,
            "teamCount": int(target[0]) if target is not None else None,
            "baseline": baseline is not None,
            "locallyOwned": owned is not None,
            "candidateHashAlreadyKnown": known_hash is not None,
        }
        row["valuableGold"] = bool(
            target is not None
            and int(target[0]) == 0
            and baseline is None
            and owned is None
            and known_hash is None
        )


def select_manifest_rows(rows: Iterable[dict]) -> list[dict]:
    best: dict[tuple[str, int], dict] = {}
    for row in rows:
        if not row.get("valuableGold"):
            continue
        line = str(row["coefficientLine"])
        digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
        if digest != str(row["coefficientSha256"]):
            raise ValueError(
                f"candidate hash mismatch for {row['targetLabel']}/r{row['targetR']}"
            )
        key = (str(row["targetLabel"]), int(row["targetR"]))
        incumbent = best.get(key)
        if incumbent is None or int(row["polynomialDiscriminantAbs"]) < int(
            incumbent["polynomialDiscriminantAbs"]
        ):
            best[key] = row
    selected = sorted(
        best.values(), key=lambda row: (int(row["targetT"]), int(row["targetR"]))
    )
    lines = [str(row["coefficientLine"]) for row in selected]
    if len(lines) != len(set(lines)):
        raise ValueError("duplicate coefficient lines selected for manifest")
    return selected


def write_jsonl_atomic(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def result_sort_key(row: dict) -> tuple:
    label = str(row.get("sourceLabel", "24T999999999"))
    label_t = int(label[3:]) if re.fullmatch(r"24T[0-9]+", label) else 999999999
    field_disc = row.get("sourceFieldDiscAbs")
    return (
        label_t,
        int(row.get("sourceR", 99)),
        int(field_disc) if field_disc is not None else 0,
        str(row.get("sourceSubmissionId", "")),
        int(row.get("sourcePolynomialIndex", -1)),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        type=parse_source_pair,
        metavar="24Tn/rN",
        help="explicit source label and real-root signature; repeat as needed",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--transforms", default="1,2,3,5,7")
    parser.add_argument("--reduce", choices=("none", "best", "abs"), default="best")
    parser.add_argument(
        "--candidate-output", type=Path, default=DEFAULT_CANDIDATE_OUTPUT
    )
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY_OUTPUT)
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_MANIFEST_OUTPUT)
    parser.add_argument(
        "--tested-input",
        action="append",
        type=Path,
        default=[],
        help="additional retained candidate JSONL whose source hashes must be skipped",
    )
    parser.add_argument(
        "--allow-no-live-gold-label",
        action="store_true",
        help="allow research tasks whose forced label currently has no live gold signature",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    transform_values = [
        value.strip() for value in args.transforms.split(",") if value.strip()
    ]
    if not transform_values:
        parser.error("--transforms must contain at least one integer")
    try:
        [int(value) for value in transform_values]
    except ValueError:
        parser.error("--transforms must be a comma-separated list of integers")

    validate_output_paths(
        args.candidate_output, args.summary_output, args.manifest_output
    )
    existing_rows = read_jsonl(args.candidate_output)
    tested_inputs = [
        *DEFAULT_TESTED_INPUTS,
        *args.tested_input,
        args.candidate_output,
    ]
    orbits = load_orbits()
    connection = read_only_connection()
    try:
        tested_hashes, tested_rows_by_path = load_tested_source_hashes(
            connection, tested_inputs
        )
        routes = validate_routes(
            connection,
            args.source,
            orbits,
            require_live_gold_label=not args.allow_no_live_gold_label,
        )
        tasks, source_diagnostics = select_source_tasks(
            connection, routes, tested_hashes
        )
    finally:
        connection.close()

    new_results = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, args.workers)
    ) as executor:
        futures = {
            executor.submit(
                run_task,
                task,
                timeout=args.timeout,
                transforms=args.transforms,
                reduction=args.reduce,
            ): task
            for task in tasks
        }
        for index, future in enumerate(
            concurrent.futures.as_completed(futures), start=1
        ):
            row = future.result()
            new_results.append(row)
            print(
                f"completed {index}/{len(tasks)}: {row['sourceLabel']} "
                f"r={row['sourceR']} nfdisc={row['sourceFieldDiscAbs']} "
                f"status={row.get('status')}",
                file=sys.stderr,
                flush=True,
            )

    combined = [*existing_rows, *new_results]
    connection = read_only_connection()
    try:
        annotate_current_target_state(connection, combined)
    finally:
        connection.close()
    combined.sort(key=result_sort_key)
    selected = select_manifest_rows(combined)

    manifest_text = "".join(
        f"{row['coefficientLine']}\n" for row in selected
    )
    summary = {
        "requestedSourcePairs": [
            {"label": label, "r": r}
            for label, r in sorted(
                set(args.source), key=lambda item: (int(item[0][3:]), item[1])
            )
        ],
        "sourceDiagnostics": source_diagnostics,
        "testedSourceHashes": len(tested_hashes),
        "testedRowsByPath": tested_rows_by_path,
        "selectedSourceTasks": len(tasks),
        "existingCandidateRows": len(existing_rows),
        "newResults": len(new_results),
        "newCertified": sum(
            row.get("status") == "certified" for row in new_results
        ),
        "newFailed": sum(
            row.get("status") != "certified" for row in new_results
        ),
        "retainedCandidateRows": len(combined),
        "manifestGoldPairs": len(selected),
        "grossGoldUpperBoundPoints": len(selected),
        "candidateOutput": str(args.candidate_output),
        "summaryOutput": str(args.summary_output),
        "manifestOutput": str(args.manifest_output),
        "selectedPairs": [
            {
                "label": row["targetLabel"],
                "r": int(row["targetR"]),
                "sourceLabel": row["sourceLabel"],
                "sourceR": int(row["sourceR"]),
                "sourceFieldDiscAbs": row["sourceFieldDiscAbs"],
                "coefficientSha256": row["coefficientSha256"],
            }
            for row in selected
        ],
    }
    write_jsonl_atomic(args.candidate_output, combined)
    write_text_atomic(args.manifest_output, manifest_text)
    write_text_atomic(
        args.summary_output,
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["newFailed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
