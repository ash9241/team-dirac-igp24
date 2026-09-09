#!/usr/bin/env python3
"""Merge one fully verified submission into an existing pending shadow score."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from build_pending_shadow_score_20260813 import nfdisc


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--submission", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--gp", type=Path, default=Path.home() / ".local" / "bin" / "gp")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--gp-stack", type=int, default=67108864)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)

    base = json.loads(args.base.read_text())
    by_pair = {(str(row["label"]), int(row["r"])): row for row in base["pairs"]}
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in args.submission)
    rows = list(connection.execute(
        f"""
        SELECT v.label,v.r,v.submission_id,v.polynomial_index,p.coefficients,
               p.coefficient_hash,t.team_count,t.discovered,t.minimum_disc_abs
        FROM verifications v JOIN polynomials p USING(submission_id,polynomial_index)
        JOIN targets t ON t.label=v.label AND t.r=v.r
        LEFT JOIN baseline_pairs b ON b.label=v.label AND b.r=v.r
        WHERE v.submission_id IN ({placeholders}) AND v.status='accepted' AND v.scoring_status='pending'
          AND b.label IS NULL
          AND NOT EXISTS(
              SELECT 1 FROM verifications owned
              WHERE owned.label=v.label AND owned.r=v.r AND owned.scoreable=1
          )
        """,
        args.submission,
    ))
    connection.close()
    unique = {str(row["coefficient_hash"]): str(row["coefficients"]) for row in rows}

    def compute(item: tuple[str, str]) -> tuple[str, int]:
        digest, coefficients = item
        return digest, nfdisc(args.gp, coefficients, args.timeout, args.gp_stack)

    discs: dict[str, int] = {}
    failures: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(compute, item): item[0] for item in unique.items()}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            digest = futures[future]
            try:
                actual, value = future.result()
                discs[actual] = value
            except Exception as error:
                failures[digest] = f"{type(error).__name__}: {error}"
            if index % 100 == 0 or index == len(unique):
                print(json.dumps({"completed": index, "failures": len(failures), "total": len(unique)}), flush=True)

    additions: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        digest = str(row["coefficient_hash"])
        if digest not in discs:
            continue
        additions[(str(row["label"]), int(row["r"]))].append({
            "candidateDiscAbs": discs[digest],
            "coefficientSha256": digest,
            "polynomialIndex": int(row["polynomial_index"]),
            "submissionId": str(row["submission_id"]),
            "teamCount": int(row["team_count"]),
            "discovered": bool(row["discovered"]),
            "incumbentMinimumDiscAbs": int(row["minimum_disc_abs"]) if row["minimum_disc_abs"] is not None else None,
        })

    for pair, candidates in additions.items():
        previous = by_pair.get(pair)
        if previous is not None:
            candidates.append(previous["bestCandidate"])
        best = min(candidates, key=lambda row: (int(row["candidateDiscAbs"]), str(row["coefficientSha256"])))
        team_count = int(best["teamCount"])
        base_points = 2.0 ** (-team_count)
        incumbent = best.get("incumbentMinimumDiscAbs")
        ratio = 1.0 if team_count == 0 or incumbent is None else min(1.0, math.log(int(incumbent)) / math.log(int(best["candidateDiscAbs"])))
        by_pair[pair] = {
            "label": pair[0], "r": pair[1],
            "acceptedPendingCandidates": len(candidates),
            "bestCandidate": best,
            "observedCurrentTeamCount": team_count,
            "eventualTeamCount": team_count + 1,
            "contentionBase": base_points,
            "discriminantRatio": ratio,
            "projectedPoints": base_points * ratio,
        }

    pairs = sorted(by_pair.values(), key=lambda row: (-row["projectedPoints"], row["label"], row["r"]))
    total = sum(float(row["projectedPoints"]) for row in pairs)
    ceiling = sum(float(row["contentionBase"]) for row in pairs)
    current = float(base["currentScore"])
    payload = {
        "schemaVersion": "pending-shadow-score-v1-incremental",
        "audit": {
            "base": str(args.base),
            "incrementalSubmissions": args.submission,
            "incrementalRows": len(rows),
            "incrementalDistinctHashes": len(unique),
            "incrementalDistinctPairs": len(additions),
            "nfdiscFailures": len(failures),
            "nfdiscFailureDetails": failures,
            "distinctPairs": len(pairs),
        },
        "currentScore": current,
        "projectedPendingPoints": total,
        "projectedScoreAfterBacklog": current + total,
        "contentionOnlyUpperBoundPoints": ceiling,
        "contentionOnlyUpperBoundScore": current + ceiling,
        "teamCountDistribution": dict(sorted(Counter(int(row["eventualTeamCount"]) for row in pairs).items())),
        "pairs": pairs,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: payload[key] for key in ("projectedPendingPoints", "projectedScoreAfterBacklog", "contentionOnlyUpperBoundPoints", "contentionOnlyUpperBoundScore")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
