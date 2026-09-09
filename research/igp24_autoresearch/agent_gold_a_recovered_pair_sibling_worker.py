#!/usr/bin/env python3
"""Minimal recovered Cross-1000 pair-sibling worker.

The original July 13--16 worker bundle is no longer present.  This wrapper
preserves the smallest reproducible lane available in the current workspace:

1. load one already verified degree-24 source from the local ledger;
2. require a unique length-24 unordered-pair orbit in the exact GAP census;
3. invoke ``pair_sum_one.sage.py`` to build and factor the Newton-sum
   resolvent; and
4. audit the resulting coefficient hash and exact target pair against the
   ledger.

It has no network code, performs no submission, and writes no files.  Its only
output is one JSON record on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "data" / "ledger.sqlite3"
ORBIT_MAP = ROOT / "data" / "pair_orbit_map.jsonl"
SAGE_WORKER = ROOT / "pair_sum_one.sage.py"


def load_orbit_row(source_label: str) -> dict:
    for line in ORBIT_MAP.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        if str(row["sourceLabel"]) == source_label:
            return row
    raise ValueError(f"no pair-orbit census row for {source_label}")


def source_row(conn: sqlite3.Connection, submission_id: str, index: int) -> dict:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT p.coefficient_hash, v.label, v.r, v.scoreable
        FROM polynomials AS p
        JOIN verifications AS v USING (submission_id, polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (submission_id, index),
    ).fetchone()
    if row is None:
        raise ValueError("source is absent from the verified local ledger")
    if int(row["scoreable"] or 0) != 1:
        raise ValueError("source exists but is not marked scoreable")
    return dict(row)


def parse_worker_result(completed: subprocess.CompletedProcess[str]) -> dict:
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(
            f"Sage worker emitted no JSON (exit={completed.returncode}): "
            f"{completed.stderr[-1200:]}"
        )
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid Sage worker JSON: {lines[-1][-1200:]}") from exc
    if completed.returncode != 0 or result.get("status") != "certified":
        raise RuntimeError(
            f"Sage worker did not certify the candidate: exit={completed.returncode}, "
            f"status={result.get('status')}, stderr={completed.stderr[-1200:]}"
        )
    return result


def audit_candidate(conn: sqlite3.Connection, result: dict) -> dict:
    line = str(result["coefficientLine"])
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(result["coefficientSha256"]):
        raise ArithmeticError("worker coefficient hash does not recompute")

    target_label = str(result["targetLabel"])
    target_r = int(result["targetR"])
    coefficient_rows = int(
        conn.execute(
            "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
        ).fetchone()[0]
    )
    owned_pair_rows = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM verifications
            WHERE label=? AND r=? AND scoreable=1
            """,
            (target_label, target_r),
        ).fetchone()[0]
    )
    target = conn.execute(
        "SELECT t,team_count FROM targets WHERE label=? AND r=?",
        (target_label, target_r),
    ).fetchone()
    baseline = conn.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
        (target_label, target_r),
    ).fetchone()

    same_discriminant_rows = None
    field_disc = result.get("fieldDiscriminantAbs")
    if field_disc is not None:
        same_discriminant_rows = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM verifications
                WHERE label=? AND r=? AND field_disc_abs=? AND scoreable=1
                """,
                (target_label, target_r, str(field_disc)),
            ).fetchone()[0]
        )

    team_count = int(target[1]) if target is not None else None
    return {
        "coefficientSha256Verified": True,
        "ledgerCoefficientRows": coefficient_rows,
        "unseenCoefficientHash": coefficient_rows == 0,
        "ownedExactTargetPairRows": owned_pair_rows,
        "unownedExactTargetPair": owned_pair_rows == 0,
        "sameTargetFieldDiscriminantRows": same_discriminant_rows,
        "targetT": int(target[0]) if target is not None else None,
        "targetTeamCountAtLedgerSnapshot": team_count,
        "baselinePair": baseline is not None,
        "goldAtLedgerSnapshot": bool(
            target is not None
            and team_count == 0
            and baseline is None
            and owned_pair_rows == 0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("submission_id")
    parser.add_argument("polynomial_index", type=int)
    parser.add_argument("--transforms", default="1,2,3,5,7")
    parser.add_argument("--reduce", choices=("none", "best", "abs"), default="best")
    parser.add_argument("--nfdisc", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    if args.timeout < 1:
        parser.error("--timeout must be positive")
    sage = shutil.which("sage")
    if sage is None:
        raise RuntimeError("Sage executable is not on PATH")
    for required in (LEDGER, ORBIT_MAP, SAGE_WORKER):
        if not required.is_file():
            raise FileNotFoundError(required)

    with sqlite3.connect(LEDGER) as conn:
        source = source_row(conn, args.submission_id, args.polynomial_index)
        source_label = str(source["label"])
        orbit = load_orbit_row(source_label)
        targets = list(orbit.get("targets") or [])
        if len(targets) != 1:
            raise ValueError(
                f"minimal recovered worker requires exactly one length-24 orbit; "
                f"{source_label} has {len(targets)}"
            )
        expected_target = str(targets[0]["targetLabel"])
        command = [
            sage,
            "-python",
            str(SAGE_WORKER),
            args.submission_id,
            str(args.polynomial_index),
            "--expected-target",
            expected_target,
            "--transforms",
            args.transforms,
            "--reduce",
            args.reduce,
        ]
        if args.nfdisc:
            command.append("--nfdisc")
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            check=False,
        )
        result = parse_worker_result(completed)
        audit = audit_candidate(conn, result)

    output = {
        "status": "certified",
        "recoveryWorker": str(Path(__file__).resolve()),
        "sourceSubmissionId": args.submission_id,
        "sourcePolynomialIndex": args.polynomial_index,
        "sourceLabel": source_label,
        "sourceR": int(source["r"]),
        "sourceCoefficientSha256": str(source["coefficient_hash"]),
        "targetLabel": str(result["targetLabel"]),
        "targetR": int(result["targetR"]),
        "coefficientLine": str(result["coefficientLine"]),
        "coefficientSha256": str(result["coefficientSha256"]),
        "fieldDiscriminantAbs": result.get("fieldDiscriminantAbs"),
        "transform": result["transform"],
        "orbitCertificate": result["orbitCertificate"],
        "resolventSha256": result["attempts"][-1]["resolventSha256"],
        "audit": audit,
        "sageCommand": command,
    }
    print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired as exc:
        print(json.dumps({"status": "timeout", "seconds": exc.timeout}), file=sys.stderr)
        raise SystemExit(2) from exc
