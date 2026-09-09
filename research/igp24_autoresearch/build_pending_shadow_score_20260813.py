#!/usr/bin/env python3
"""Estimate the score of accepted submissions stuck at discriminant_pending.

The server's group/signature verifier updates submission receipts before the
exact discriminant worker finishes.  This audit independently computes PARI
``nfdisc`` values, deduplicates pending candidates by pair, excludes pairs
already scoreable for Team Dirac or present in the frozen baseline, and applies
the contest's holder-count/log-discriminant formula.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import sqlite3
import subprocess
import threading
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_GP = Path.home() / ".local" / "bin" / "gp"


def nfdisc(gp: Path, coefficients: str, timeout: int, stack_size: int) -> int:
    program = f"p=Polrev([{coefficients}]);print(abs(nfdisc(p)));\n"
    completed = subprocess.run(
        [str(gp), "-q", "-f", "-s", str(stack_size)],
        input=program,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode or len(lines) != 1 or not lines[0].isdigit():
        raise ArithmeticError(
            f"gp nfdisc failed: rc={completed.returncode} "
            f"stdout={completed.stdout[-300:]!r} stderr={completed.stderr[-300:]!r}"
        )
    value = int(lines[0])
    if value <= 1:
        raise ArithmeticError(f"invalid field discriminant: {value}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--gp", type=Path, default=DEFAULT_GP)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--gp-stack", type=int, default=268435456)
    parser.add_argument("--current-score", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    if not args.gp.is_file() or args.workers < 1 or args.timeout < 1:
        raise ValueError("invalid PARI executable or worker settings")

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = list(
        connection.execute(
            """
            WITH scored AS (
                SELECT DISTINCT label,r
                FROM verifications WHERE scoreable=1
            )
            SELECT v.label,v.r,v.submission_id,v.polynomial_index,
                   p.coefficients,p.coefficient_hash,
                   t.team_count,t.discovered,t.minimum_disc_abs
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            JOIN targets AS t ON t.label=v.label AND t.r=v.r
            LEFT JOIN scored AS s ON s.label=v.label AND s.r=v.r
            LEFT JOIN baseline_pairs AS b ON b.label=v.label AND b.r=v.r
            WHERE v.status='accepted' AND v.scoring_status='pending'
              AND v.label IS NOT NULL AND v.r IS NOT NULL
              AND s.label IS NULL AND b.label IS NULL
            ORDER BY v.label,v.r,v.submission_id,v.polynomial_index
            """
        )
    )
    connection.close()

    unique: dict[str, str] = {}
    for row in rows:
        coefficients = str(row["coefficients"])
        digest = hashlib.sha256(coefficients.encode("ascii")).hexdigest()
        recorded = str(row["coefficient_hash"])
        if recorded != digest:
            raise ValueError(f"coefficient hash mismatch for {recorded}")
        unique.setdefault(digest, coefficients)

    cache: dict[str, int] = {}
    failures: dict[str, str] = {}
    lock = threading.Lock()
    completed_count = 0

    def compute(item: tuple[str, str]) -> tuple[str, int]:
        digest, coefficients = item
        return digest, nfdisc(args.gp, coefficients, args.timeout, args.gp_stack)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(compute, item): item[0] for item in unique.items()}
        for future in concurrent.futures.as_completed(futures):
            digest = futures[future]
            try:
                actual_digest, value = future.result()
                if actual_digest != digest:
                    raise RuntimeError("worker identity mismatch")
                cache[digest] = value
            except Exception as error:
                failures[digest] = f"{type(error).__name__}: {error}"
            with lock:
                completed_count += 1
                if completed_count % 250 == 0 or completed_count == len(unique):
                    print(
                        json.dumps(
                            {
                                "event": "nfdisc_progress",
                                "completed": completed_count,
                                "total": len(unique),
                                "failures": len(failures),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )

    by_pair: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        digest = str(row["coefficient_hash"])
        if digest not in cache:
            continue
        by_pair[(str(row["label"]), int(row["r"]))].append(
            {
                "candidateDiscAbs": cache[digest],
                "coefficientSha256": digest,
                "polynomialIndex": int(row["polynomial_index"]),
                "submissionId": str(row["submission_id"]),
                "teamCount": int(row["team_count"]),
                "discovered": bool(row["discovered"]),
                "incumbentMinimumDiscAbs": (
                    int(row["minimum_disc_abs"])
                    if row["minimum_disc_abs"] is not None
                    else None
                ),
            }
        )

    pair_results = []
    total = 0.0
    contention_ceiling = 0.0
    for (label, r), candidates in sorted(by_pair.items()):
        best = min(candidates, key=lambda row: (row["candidateDiscAbs"], row["coefficientSha256"]))
        incumbent_holders = int(best["teamCount"])
        eventual_holders = incumbent_holders + 1
        # The target snapshot counts scoreable incumbent teams only.  A row
        # stuck at discriminant_pending is not yet included: this is directly
        # witnessed by pending 24T12290/r20 remaining team_count=0.  Joining a
        # current k-holder pair therefore gives base 2^-k.
        base = 2.0 ** (-incumbent_holders)
        incumbent = best["incumbentMinimumDiscAbs"]
        # With no other holder, this candidate becomes the pair minimum and
        # receives the full unique-held point.  Otherwise use the incumbent
        # minimum that existed before the stalled candidate is scoreable.
        ratio = (
            1.0
            if incumbent_holders == 0 or incumbent is None
            else min(1.0, math.log(incumbent) / math.log(best["candidateDiscAbs"]))
        )
        points = base * ratio
        total += points
        contention_ceiling += base
        pair_results.append(
            {
                "label": label,
                "r": r,
                "acceptedPendingCandidates": len(candidates),
                "bestCandidate": best,
                "observedCurrentTeamCount": incumbent_holders,
                "eventualTeamCount": eventual_holders,
                "contentionBase": base,
                "discriminantRatio": ratio,
                "projectedPoints": points,
            }
        )

    payload = {
        "schemaVersion": "pending-shadow-score-v1",
        "audit": {
            "candidateRows": len(rows),
            "distinctCandidateHashes": len(unique),
            "distinctPairs": len(by_pair),
            "nfdiscFailures": len(failures),
            "nfdiscFailureDetails": failures,
            "networkCalls": 0,
            "scoringFormula": (
                "2^(-currentIncumbentTeamCount) * "
                "min(1, log(incumbentMinimumDiscAbs)/log(candidateDiscAbs)); "
                "unique-held pairs use ratio 1"
            ),
            "exclusions": [
                "frozen baseline pairs",
                "pairs already scoreable for Team Dirac",
                "rejected or non-pending rows",
            ],
        },
        "currentScore": args.current_score,
        "projectedPendingPoints": total,
        "projectedScoreAfterBacklog": args.current_score + total,
        "contentionOnlyUpperBoundPoints": contention_ceiling,
        "contentionOnlyUpperBoundScore": args.current_score + contention_ceiling,
        "teamCountDistribution": dict(
            sorted(Counter(row["eventualTeamCount"] for row in pair_results).items())
        ),
        "pairs": sorted(pair_results, key=lambda row: (-row["projectedPoints"], row["label"], row["r"])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "candidateRows": len(rows),
                "distinctCandidateHashes": len(unique),
                "distinctPairs": len(by_pair),
                "nfdiscFailures": len(failures),
                "projectedPendingPoints": total,
                "projectedScoreAfterBacklog": args.current_score + total,
                "contentionOnlyUpperBoundPoints": contention_ceiling,
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
